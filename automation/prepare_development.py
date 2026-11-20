#!/usr/bin/env python3
"""Freeze public inputs for development authoring; never render or publish docs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from release_source import select_source, validate_frozen, release_identity

API = 'https://api.github.com/repos/pubky/paykit-rs'
RAW = 'https://raw.githubusercontent.com/pubky/paykit-rs'
PACKAGES = ('paykit-lib', 'paykit-sdk', 'paykit-ffi')
MAX_BYTES = 2_000_000


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Public-source HTTP redirects are not allowed')


def empty_output(output):
    if (not output.is_absolute() or '..' in output.parts or any(p.is_symlink() for p in (output, *output.parents))
            or (output.exists() and (not output.is_dir() or any(output.iterdir())))):
        raise ValueError('Output must be an empty task-owned absolute directory without symlinks')


def prepare_development(root, output, commit, selection=None):
    root, output = Path(root), Path(output)
    empty_output(output)
    read = lambda path: json.loads((root / path).read_bytes())
    policy = read('docs-generation.json')['source']
    previous = read('content/docs/coming-soon.json')['version']['commit']
    blueprint = read('automation/blueprints/coming-soon.json')
    if any(not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{40}', sha) for sha in (commit, previous)):
        raise ValueError('Invalid commit: expected literal 40 lowercase hexadecimal characters')
    frozen = (json.loads(Path(selection).read_bytes()) if selection is not None
              else select_source(policy, lambda *args: commit))
    source = frozen['sourceSelection']
    validate_frozen(policy, frozen, commit)
    kind, ref = source['kind'], source['ref']
    if source['repository'] != 'pubky/paykit-rs' or kind not in ('tag', 'branch'):
        raise ValueError('Unsupported public source repository or ref kind')
    qualified = 'refs/' + ('tags/' if kind == 'tag' else 'heads/') + ref
    if subprocess.run(['git', 'check-ref-format', qualified], capture_output=True).returncode:
        raise ValueError('Invalid source ref')
    artifacts = {}
    def fetch(url, name, missing=False):
        request = urllib.request.Request(url, headers={
            'User-Agent': 'paykit-docs-source-preparation', 'Accept': 'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                status, data = response.status, response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            if not missing or error.code != 404:
                raise
            status, data = error.code, error.read(MAX_BYTES + 1)
        if status != 200 and not (missing and status == 404):
            raise ValueError(f'Unexpected public HTTP status {status}: {url}')
        if len(data) > MAX_BYTES:
            raise ValueError('Public response too large: ' + url)
        if name.endswith(('.rs', '.md', 'Cargo.toml')) and ('\x00' in data.decode('utf-8')):
            raise ValueError('Source file is binary, not text: ' + name)
        artifacts[name] = data
        return status, data
    def record(url, name):
        return json.loads(fetch(url, name)[1])
    if frozen is not None and 'selection' in policy:
        release = frozen['release']
        receipt = record(API + '/releases/' + str(release['id']), 'release.json')
        if receipt['draft'] is not False or release_identity(receipt) != release:
            raise ValueError('Public release no longer matches the frozen selection')
    selected = record(API + '/git/ref/' + urllib.parse.quote(qualified[5:], safe='/'), 'ref.json')
    target = selected['object']
    object_sha = target['sha']
    if selected['ref'] != qualified or not re.fullmatch('[0-9a-f]{40}', object_sha):
        raise ValueError('Invalid exact ref receipt')
    seen = set()
    while target['type'] == 'tag':
        sha = target['sha']
        if sha in seen or not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Invalid or cyclic tag object')
        tag = record(API + '/git/tags/' + sha, 'tag.json' if not seen else f'tag-{sha}.json')
        seen.add(sha)
        if tag['sha'] != sha:
            raise ValueError('Mismatched tag object')
        target = tag['object']
    if target['type'] != 'commit' or target['sha'] != commit:
        raise ValueError('Source ref moved or did not resolve to the expected commit')
    compare = record(API + f'/compare/{previous}...{commit}', 'compare.json')
    if compare['base_commit']['sha'] != previous or len(compare['files']) >= 300:
        raise ValueError('Wrong compare base or potentially truncated compare (300-file API limit)')
    paths = {row['path'] for row in blueprint['evidence']}
    paths.update(('README.md', 'CHANGELOG.md', 'skills/paykit/SKILL.md'))
    paths.update(package + '/Cargo.toml' for package in PACKAGES)
    paths.update(row['filename'] for row in compare['files'] if row['status'] != 'removed'
                 and (row['filename'].endswith(('.rs', '.md')) or Path(row['filename']).name == 'Cargo.toml'))
    for path in paths:
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+', path) or any(part in ('', '.', '..', '.git') for part in path.split('/'))
                or path.split('/')[0] in ('previous', 'registry')
                or not (path.endswith(('.rs', '.md')) or Path(path).name == 'Cargo.toml')):
            raise ValueError('Unsafe source path: ' + path)
    files = []
    for path in sorted(paths):
        url = f'{RAW}/{commit}/' + urllib.parse.quote(path, safe='/')
        data = fetch(url, path)[1]
        files.append({'path': path, 'url': url, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    old_path = 'previous/paykit-lib/Cargo.toml'
    old_url = f'{RAW}/{previous}/paykit-lib/Cargo.toml'
    old_data = fetch(old_url, old_path)[1]
    version = {'label': ref, 'ref': ref, kind: ref, 'kind': kind, 'qualifiedRef': qualified,
               'object': object_sha, 'commit': commit,
               'sourceUrl': f'https://github.com/pubky/paykit-rs/tree/{commit}', 'packages': {}}
    if frozen is not None and 'selection' in policy:
        version['release'] = frozen['release']
    observations = []
    for package in PACKAGES:
        manifest = tomllib.loads(artifacts[package + '/Cargo.toml'].decode())
        info = manifest['package']
        if info['name'] != package or not all(isinstance(info[key], str) and info[key] for key in ('version', 'rust-version')):
            raise ValueError('Unsupported package manifest identity or inherited version')
        url = f'https://crates.io/api/v1/crates/{package}/' + urllib.parse.quote(info['version'], safe='')
        body_path = f'registry/{package}.body'
        status, data = fetch(url, body_path, missing=True)
        observations.append({'url': url, 'status': status, 'bodyPath': body_path})
        version['packages'][package] = {'version': info['version'], 'rustVersion': info['rust-version'],
            'dependencies': manifest.get('dependencies', {}), 'published': status == 200}
        if status == 200:
            registry = json.loads(data)['version']
            if (registry['crate'] != package or registry['num'] != info['version']
                    or type(registry['yanked']) is not bool or not re.fullmatch('[0-9a-f]{64}', registry['checksum'])):
                raise ValueError('Registry returned a different or malformed package')
            version['packages'][package].update(yanked=registry['yanked'], checksum=registry['checksum'],
                referenceUrl=f'https://docs.rs/{package}/{info["version"]}/{package.replace("-", "_")}/')
    index = {**source, 'commit': commit, 'previousCommit': previous, 'files': files, 'version': version,
             'previousLibraryManifest': {'commit': previous, 'path': old_path, 'url': old_url,
                                         'sha256': hashlib.sha256(old_data).hexdigest()}}
    if 'selection' in policy:
        index['sourcePolicy'] = policy
    artifacts['index.json'] = (json.dumps(index, indent=2) + '\n').encode()
    artifacts['registry-observations.json'] = (json.dumps(observations, indent=2) + '\n').encode()
    empty_output(output)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in artifacts.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(data)
    return {'action': 'prepared', 'sources': str(output), 'commit': commit, 'files': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Empty task-owned absolute source directory')
    parser.add_argument('--commit', required=True, help='Expected exact 40-character commit from the weekly check')
    parser.add_argument('--selection', type=Path, help='Weekly gate JSON: reuse its frozen release, never rediscover newest')
    args = parser.parse_args()
    try:
        result = prepare_development(Path(__file__).resolve().parents[1], args.output, args.commit, args.selection)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'action': 'error', 'reason': 'source_preparation_failed', 'detail': str(error)}))
        raise SystemExit(1)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
