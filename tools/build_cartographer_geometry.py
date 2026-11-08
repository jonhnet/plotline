#!/usr/bin/env python3
"""Build/install the optional Rust kernel into the invoking Map Studio venv.

Link to Shapely's exact GEOS, not a different system GEOS. The extension lives
next to shapely.libs and has a relative runtime search path to that directory.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig

import shapely

ROOT=Path(__file__).resolve().parents[1]


def main():
    if sys.prefix==sys.base_prefix:raise SystemExit('Run with the Map Studio virtualenv Python')
    libs=Path(shapely.__file__).resolve().parent.parent/'shapely.libs'
    matches=list(libs.glob('libgeos_c-*.so.*'))
    if len(matches)!=1:raise SystemExit('Expected the pinned Linux Shapely wheel with one bundled GEOS C library')
    build=ROOT/'build/cartographer-native';link=build/'link';link.mkdir(parents=True,exist_ok=True)
    alias=link/'libgeos_c.so';alias.unlink(missing_ok=True);alias.symlink_to(matches[0])
    env=dict(os.environ,GEOS_LIB_DIR=str(link),GEOS_VERSION=shapely.geos_version_string,
             PYO3_PYTHON=sys.executable,
             RUSTFLAGS=os.environ.get('RUSTFLAGS','')+' -C link-arg=-Wl,--disable-new-dtags,-rpath,$ORIGIN/shapely.libs')
    target_dir=Path(os.environ.get('PLOTLINE_GEOMETRY_TARGET_DIR',str(build/'target')))
    subprocess.run(['cargo','build','--release','--locked','--manifest-path',str(ROOT/'native/cartographer-geometry/Cargo.toml'),
                    '--target-dir',str(target_dir)],env=env,check=True)
    target=Path(sysconfig.get_path('platlib'))/('_cartographer_geometry'+sysconfig.get_config_var('EXT_SUFFIX'))
    temporary=target.with_suffix('.tmp');shutil.copyfile(target_dir/'release/lib_cartographer_geometry.so',temporary)
    os.replace(temporary,target)
    subprocess.run([sys.executable,'-c','import _cartographer_geometry as g; print("Installed map geometry: GEOS",g.geos_version())'],check=True)


if __name__=='__main__':main()
