#!/usr/bin/env python3
"""Require runtime Python modules and their local imports in the Git index.

Uses AST only: importing application code would read configuration or start tasks.
Ignored files are NOT acceptable runtime dependencies. Use --root for test fixtures.
"""
from __future__ import annotations
import argparse
import ast
import importlib.util
from pathlib import Path
import subprocess

PACKAGE_PATH=Path('prototype/hls-companion')

def check(root: Path) -> list[str]:
    package_root=root/PACKAGE_PATH
    directory=package_root/'companion'
    proc=subprocess.run(['git','-C',str(root),'ls-files','-z'],check=True,capture_output=True)
    tracked=set(proc.stdout.decode('utf-8').split(chr(0)))
    errors=set()
    if not directory.is_dir(): return ['runtime companion package is missing']
    def require(path, importer):
        relative=path.relative_to(root).as_posix()
        if not path.is_file(): errors.add(f'{importer}: missing {relative}')
        elif relative not in tracked: errors.add(f'{importer}: untracked runtime dependency {relative}')
    def module_path(name):
        base=package_root.joinpath(*name.split('.'))
        file=base.with_suffix('.py')
        return file if file.is_file() else base/'__init__.py'
    for file in directory.rglob('*.py'):
        relative=file.relative_to(root).as_posix()
        require(file,relative)
        try: tree=ast.parse(file.read_text(encoding='utf-8-sig'),filename=relative)
        except (ValueError,SyntaxError) as error:
            errors.add(f'{relative}: invalid Python source: {error}'); continue
        parts=list(file.relative_to(package_root).with_suffix('').parts)
        package='.'.join(parts[:-1])
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                names=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom):
                if node.level:
                    try: name=importlib.util.resolve_name('.'*node.level+(node.module or ''),package)
                    except ImportError:
                        errors.add(f'{relative}:{node.lineno}: invalid relative import'); continue
                else: name=node.module or ''
                names=[name]
                for alias in node.names:
                    candidate=name+'.'+alias.name
                    if module_path(candidate).is_file(): names.append(candidate)
            else: continue
            for name in names:
                if name=='companion' or name.startswith('companion.'):
                    require(module_path(name),f'{relative}:{node.lineno}')
    for file in (directory/'data').glob('*.json'):
        require(file,'runtime data')
    return sorted(errors)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args=parser.parse_args()
    errors=check(args.root.resolve())
    for error in errors: print('FAIL',error)
    if not errors: print('PASS all runtime Python modules, local imports and language data are tracked')
    return bool(errors)

if __name__=='__main__': raise SystemExit(main())
