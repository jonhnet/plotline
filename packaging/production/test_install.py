"""Run from an exported snapshot: python3 packaging/production/test_install.py."""
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import uuid

import common
from build import tree_hash
from install import checked_receipt


class PublicationSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.c={'data_root':str(self.root),'web_bind':'127.0.0.1:19191','map_port':19193,'ipp_port':19192}

    def save(self,path,value):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(value));return target

    def test_admission_closes_race_and_preserves_inode(self):
        path=self.root/'admission.lock';path.touch();before=path.stat().st_ino
        with path.open('rb') as request:
            fcntl.flock(request,fcntl.LOCK_SH)
            with self.assertRaises(common.Busy):
                with common.admission(self.c): self.fail('An active request admitted an installer')
        with common.admission(self.c):
            with path.open('rb') as request:
                with self.assertRaises(BlockingIOError):fcntl.flock(request,fcntl.LOCK_SH|fcntl.LOCK_NB)
        self.assertEqual(path.stat().st_ino,before)

    def test_conversion_and_uncertain_physical_completion_refuse(self):
        self.save('studios/conversions/one/record.json',{'status':'running'})
        self.assertIn('conversions/one is running',common.durable_busy(self.c))
        # Stopped workers can recover their interrupted conversion on boot.
        self.assertEqual(common.durable_busy(self.c,False,False),[])
        identity=str(uuid.uuid4());self.save('studios/plots/active.json',identity)
        record=self.save(f'studios/plots/{identity}/record.json',{'phase':'accepted','execution':{'state':'feeding'}})
        self.assertTrue(common.durable_busy(self.c,False,False),'Stopped web process does not prove the physical plot finished')
        record.write_text(json.dumps({'phase':'accepted','execution':{'state':'finished'}}))
        self.assertEqual(common.durable_busy(self.c,False,False),[])

    def test_idle_requires_bounded_map_and_ipp_observations(self):
        with patch.object(common,'active',return_value=True),patch.object(common,'read_json',return_value={'admission_protocol':1,'jobs':[{'id':'a'}]}),patch.object(common,'ipp',return_value=''):
            with self.assertRaisesRegex(common.Busy,'active renders'):common.idle(self.c)
        with patch.object(common,'active',return_value=True),patch.object(common,'read_json',return_value={'admission_protocol':1}),patch.object(common,'ipp',return_value='job-state (enum) = pending-held'):
            with self.assertRaisesRegex(common.Busy,'incomplete or processing'):common.idle(self.c)
        with patch.object(common,'active',return_value=True),patch.object(common,'read_json',side_effect=OSError('offline')),patch.object(common,'ipp',return_value=''):
            with self.assertRaisesRegex(common.Busy,'cannot be checked'):common.idle(self.c)

    def test_no_git_needed_and_export_bytes_matter(self):
        path=self.root/'source';path.mkdir();(path/'SOURCE_REVISION').write_text('a'*40)
        a=tree_hash(path);(path/'one.py').write_text('print(1)');self.assertNotEqual(a,tree_hash(path))
        expected=tree_hash(path);(path/'__pycache__').mkdir();(path/'__pycache__/one.pyc').write_bytes(b'generated')
        self.assertEqual(expected,tree_hash(path),'Running exported tests must not invalidate source identity')
        (path/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):tree_hash(path)

    def test_state_changes_are_atomic(self):
        path=self.root/'receipt.json';common.atomic(path,{'revision':'a'})
        common.atomic(path,{'revision':'b'});self.assertEqual(json.loads(path.read_text()),{'revision':'b'})
        self.assertEqual(sorted(p.name for p in self.root.iterdir()),['receipt.json'])

    def test_readiness_failure_and_recovery_are_recorded(self):
        path=self.save('installation.json',{'installed_revision':'a'*40,'ready':True})
        with patch('install.readiness',side_effect=RuntimeError('IPP unavailable')):
            with self.assertRaises(RuntimeError): checked_receipt(self.c,'a'*40)
        value=json.loads(path.read_text())
        self.assertFalse(value['ready']);self.assertEqual(value['error'],'IPP unavailable')
        with patch('install.readiness',return_value={'ipp':{'ready':True}}):
            checked_receipt(self.c,'a'*40)
        value=json.loads(path.read_text());self.assertTrue(value['ready']);self.assertNotIn('error',value)

    def test_service_manager_queries_are_bounded(self):
        with patch.object(common,'run',return_value=subprocess.CompletedProcess([],0,stdout='active\n')) as query:
            self.assertTrue(common.active('plotline-web'))
            self.assertEqual(query.call_args.kwargs['timeout'],3)


if __name__=='__main__':unittest.main()
