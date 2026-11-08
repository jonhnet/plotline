"""Offline HP7585 timing model for Plotline Studios's normalized, absolute HPGL.

A bounded FIFO overlaps serial input and command execution. Movement uses a
trapezoidal velocity profile with a geometric corner-speed approximation.
Parameters are effective fitted quantities, not hardware measurements.
"""
from dataclasses import dataclass
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from numba import njit

def read_retained(path):
    path = Path(path)
    if path.suffix != '.gz':
        try:
            return path.read_bytes()
        except FileNotFoundError:
            path = Path(str(path) + '.gz')
    with gzip.open(path, 'rb') as source:
        return source.read()


PARAMETERS = ['draw_speed_scale', 'pen_up_mm_s', 'acceleration_mm_s2',
              'lift_lower_cycle_s', 'pen_change_s', 'minimum_vector_s', 'buffer_bytes']


@dataclass
class Program:
    source: bytes
    ends: np.ndarray
    move_index: np.ndarray
    lengths: np.ndarray
    down: np.ndarray
    speeds: np.ndarray
    radius: np.ndarray
    transitions: np.ndarray
    selections: np.ndarray
    slots: np.ndarray

    @property
    def sha256(self):
        return hashlib.sha256(self.source).hexdigest()


def parse(source, units=40.):
    ends, index, lengths, down_flags, speeds, vectors = [], [], [], [], [], []
    transitions, selections, slots = [], [], []
    position, down, speed, slot, offset = None, False, 50., 0, 0
    for i, raw in enumerate(source.splitlines(keepends=True)):
        text = raw.decode('ascii').strip()
        if not raw.endswith(b';\n') or len(raw) > 80:
            raise ValueError('Use the exact normalized prepared.hpgl, with one command per line')
        op, arg = text[:2], text[2:-1]
        if op not in ('IN', 'PA', 'SP', 'VS', 'FS', 'PU', 'PD'):
            raise ValueError(f'Unsupported timing instruction: {op}')
        toggle = selection = 0
        if op == 'IN':
            if i != 0 and not (i == 1 and source.startswith(b'PU;\nIN;\n')):
                raise ValueError('Mid-program IN is outside the timing model')
        elif op == 'PA' and arg:
            raise ValueError('Normalize moves to PU/PD first')
        elif op == 'VS':
            speed = float(arg)*10
            if not 0 < speed <= 1000:
                raise ValueError('Invalid VS')
        elif op == 'SP':
            new = int(arg)
            if not 0 <= new <= 8:
                raise ValueError('Invalid slot')
            selection, slot = int(new != slot), new
        elif op in ('PU', 'PD'):
            new = op == 'PD'
            toggle, down = int(new != down), new
            if arg:
                q = np.array([float(v) for v in arg.split(',')])/units
                if len(q) != 2 or not np.all(np.isfinite(q)):
                    raise ValueError('Expected one finite XY point per move')
                delta = q-position if position is not None else np.zeros(2)
                distance = float(np.linalg.norm(delta))
                index.append(i); lengths.append(distance); down_flags.append(down)
                speeds.append(speed); vectors.append(delta/distance if distance else np.zeros(2))
                position = q
        offset += len(raw)
        ends.append(offset); transitions.append(toggle); selections.append(selection); slots.append(slot)
    if not ends or not lengths:
        raise ValueError('Empty drawing')
    lengths, vectors = np.asarray(lengths), np.asarray(vectors)
    radius = np.zeros(len(lengths)+1)
    for k in range(1, len(lengths)):
        if down_flags[k] and down_flags[k-1] and index[k] == index[k-1]+1:
            sine = math.sqrt(max(0., (1-float(np.dot(vectors[k], vectors[k-1])))/2))
            radius[k] = min(lengths[k], lengths[k-1])/(2*max(sine, 1e-12))
    return Program(source, np.asarray(ends), np.asarray(index), lengths,
                   np.asarray(down_flags), np.asarray(speeds), radius,
                   np.asarray(transitions), np.asarray(selections), np.asarray(slots))


@njit(cache=True)
def command_costs(parameters, lengths, down, speeds, radius, index, transitions, selections):
    scale, up_speed, acceleration, cycle, change, minimum, _ = parameters
    count = len(lengths)
    vmax = np.empty(count)
    velocity = np.sqrt(acceleration*radius)
    for k in range(count):
        vmax[k] = speeds[k]*scale if down[k] else up_speed
        velocity[k] = min(velocity[k], vmax[k])
        velocity[k+1] = min(velocity[k+1], vmax[k])
    for k in range(count):
        velocity[k+1] = min(velocity[k+1], math.sqrt(velocity[k]**2+2*acceleration*lengths[k]))
    for k in range(count-1, -1, -1):
        velocity[k] = min(velocity[k], math.sqrt(velocity[k+1]**2+2*acceleration*lengths[k]))
    result = transitions*(cycle/2)+selections*change
    for k in range(count):
        u, v = velocity[k], velocity[k+1]
        peak = min(vmax[k], math.sqrt(acceleration*lengths[k]+.5*(u*u+v*v)))
        ramp_distance = (2*peak*peak-u*u-v*v)/(2*acceleration)
        movement = (2*peak-u-v)/acceleration + max(0., lengths[k]-ramp_distance)/vmax[k]
        result[index[k]] += max(minimum, movement)
    return result


@njit(cache=True)
def pipeline(costs, ends, gaps, capacity, byte_rate):
    """Input and motion overlap; command completion frees buffered source bytes."""
    count = len(ends)
    arrival, finish = np.zeros(count), np.zeros(count)
    cursor, previous = 0, 0
    incoming = executed = 0.
    for i in range(count):
        boundary = ends[i]-capacity
        while cursor < i and ends[cursor] < boundary:
            cursor += 1
        freed = finish[cursor] if cursor < i and boundary > 0 else 0.
        incoming = max(incoming+gaps[i]+(ends[i]-previous)/byte_rate, freed)
        executed = max(executed, incoming)+costs[i]
        arrival[i], finish[i], previous = incoming, executed, ends[i]
    return arrival, finish


def simulate(program, parameters, gaps=None, byte_rate=960.):
    p = np.asarray(parameters, dtype=float)
    if len(p) != 7 or not np.all(np.isfinite(p)) or np.any(p[:3] <= 0) or np.any(p[3:6] < 0) or p[6] < 128:
        raise ValueError('Invalid timing parameters')
    if gaps is None:
        gaps = np.zeros(len(program.ends))
    costs = command_costs(p, program.lengths, program.down, program.speeds, program.radius,
                         program.move_index, program.transitions, program.selections)
    return pipeline(costs, program.ends, gaps, p[6], byte_rate)


@dataclass
class Capture:
    directory: Path
    program: Program
    summary: dict
    header: dict
    metadata: dict
    trace_sha256: str
    packet_ends: np.ndarray
    packet_indices: np.ndarray
    entry: np.ndarray
    drain: np.ndarray
    gap: np.ndarray
    byte_rate: float


def load_capture(directory):
    directory = Path(directory)
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('inspect_timing', root/'controller/scripts/inspect-timing.py')
    inspection = importlib.util.module_from_spec(spec); spec.loader.exec_module(inspection)
    summary = inspection.inspect(directory)
    if not summary['pi_timing_available'] or summary['truncated_final_record'] or summary['unacknowledged_write'] or summary['errors'] or not summary['closed']:
        raise ValueError('Use a closed, intact Pi timing capture')
    trace = read_retained(directory/'serial-timing.jsonl')
    entries, drains, writes, metadata = {}, {}, [], None
    header = None
    for line in trace.splitlines():
        event = json.loads(line)
        if event['event'] == 'header': header = event
        elif event['event'] == 'reply' and event['op'] == 'timing': metadata = event['result']
        elif event['event'] == 'write': writes.append(event)
        elif event['event'] == 'tx_queue_timing_v1':
            for now, previous, accepted, queued in event['samples']:
                if accepted: entries.setdefault(accepted, now/1e9)
                if accepted and queued == 0: drains.setdefault(accepted, now/1e9)
    if metadata is None:
        raise ValueError('Missing Pi clock/serial metadata')
    # A cancelled tail is useful as a prefix check. Pauses/resumes cannot be
    # silently included in movement fitting; they require a separate model.
    if any(w['kind'] not in ('prepared', 'cancel', 'cancel-query', 'completion-query') for w in writes):
        raise ValueError('Pause/resume traces require segmenting before fitting')
    ends = np.array([w['prepared_offset']+len(w['data']) for w in writes if w['kind'] == 'prepared'])
    if any(int(n) not in entries or int(n) not in drains for n in ends):
        raise ValueError('A write boundary has no accepted/empty-queue observation')
    program = parse(read_retained(directory/'prepared.hpgl'), header['metadata']['units_per_mm'])
    indices = np.searchsorted(program.ends, ends)
    if not np.array_equal(program.ends[indices], ends):
        raise ValueError('Writes split normalized commands')
    entry = np.array([entries[int(n)] for n in ends]); drain = np.array([drains[int(n)] for n in ends])
    gaps = np.zeros(len(program.ends)); gaps[0] = entry[0]
    gaps[indices[:-1]+1] = entry[1:]-drain[:-1]
    if np.any(gaps < 0): raise ValueError('Overlapping writes are outside this feeder model')
    return Capture(directory, program, summary, header, metadata, hashlib.sha256(trace).hexdigest(),
                   ends, indices, entry, drain, gaps, metadata['baud']/metadata['bits_per_byte'])


def packetize(program, turnaround):
    """Use the same whole-command, <=512-byte packing as Plotline Studios."""
    gaps = np.zeros(len(program.ends)); ends = []; start = 0
    for i, end in enumerate(program.ends):
        if end-start > 512:
            ends.append(i-1); start = program.ends[i-1]; gaps[i] = turnaround
    ends.append(len(program.ends)-1)
    return np.asarray(ends), gaps
