"""Fetch unchanged public Docker Hub images through an existing local proxy.

Windows curl is only a network transport. All Python, archive verification,
Docker import, and experiment execution remain WSL-native. No system settings
are modified, and no RepoFix API credential is loaded by this script.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/public-image-transfer'
CURL = '/mnt/c/Windows/System32/curl.exe'
PROXY = 'http://127.0.0.1:17891'
ACCEPT = ', '.join(['application/vnd.docker.distribution.manifest.v2+json',
                    'application/vnd.docker.distribution.manifest.list.v2+json',
                    'application/vnd.oci.image.manifest.v1+json',
                    'application/vnd.oci.image.index.v1+json'])


def request(url, token=None, output=None):
    # Anonymous Registry pull token travels through stdin, not argv or logs.
    cfg = ['silent', 'show-error', 'fail', 'location', 'connect-timeout = 15',
           'max-time = 900', 'retry = 1', 'proxy = ' + json.dumps(PROXY),
           'url = ' + json.dumps(url), 'header = ' + json.dumps('Accept: ' + ACCEPT)]
    if token:
        cfg.append('header = ' + json.dumps('Authorization: Bearer ' + token))
    if output:
        windows = subprocess.check_output(['wslpath','-w',str(output)], text=True).strip()
        cfg.append('output = ' + json.dumps(windows))
    result = subprocess.run([CURL,'--config','-'], input=('\n'.join(cfg)+'\n').encode(),
                            capture_output=True, check=False)
    if result.returncode:
        # Do not print potential redirect/signed URLs or authorization tokens.
        raise RuntimeError(f'public image download failed: curl exit {result.returncode}')
    return result.stdout


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('image')
    args = parser.parse_args()
    repository, tag = args.image.rsplit(':', 1)
    if '/' not in repository:
        repository = 'library/' + repository
    assert repository == 'library/python' or repository.startswith('swebench/sweb.eval.x86_64.django_')
    CACHE.mkdir(parents=True, exist_ok=True)
    token = json.loads(request('https://auth.docker.io/token?service=registry.docker.io&scope=repository:' + repository + ':pull'))['token']
    base = 'https://registry-1.docker.io/v2/' + repository
    manifest_bytes = request(base + '/manifests/' + tag, token)
    index_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    if 'manifests' in manifest:
        chosen = next(m for m in manifest['manifests'] if m.get('platform', {}).get('os') == 'linux' and m['platform'].get('architecture') == 'amd64')
        manifest_bytes = request(base + '/manifests/' + chosen['digest'], token)
        assert hashlib.sha256(manifest_bytes).hexdigest() == chosen['digest'].split(':')[1]
        manifest = json.loads(manifest_bytes)
    folder = CACHE / repository.replace('/', '_')
    folder.mkdir(exist_ok=True)
    (folder / 'manifest.json').write_bytes(manifest_bytes)
    metadata = dict(image=args.image, index_sha256=index_digest,
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                    source='Docker Hub official repository; existing Windows local proxy used only for transport',
                    compressed_bytes=sum(x['size'] for x in manifest['layers']),
                    config_digest=manifest['config']['digest'])
    print(json.dumps(metadata), flush=True)
    (folder / 'provenance.json').write_text(json.dumps(metadata,indent=2))
    for item in [manifest['config'], *manifest['layers']]:
        expected = item['digest'].split(':')[1]
        path = CACHE / expected
        if path.exists() and digest(path) == expected:
            print('cache_hit ' + expected, flush=True)
            continue
        print(f'download {expected} bytes={item["size"]}', flush=True)
        request(base + '/blobs/' + item['digest'], token, path)
        assert digest(path) == expected, 'blob SHA256 mismatch'
    config_name = manifest['config']['digest'].split(':')[1] + '.json'
    layer_names = [x['digest'].split(':')[1] + '/layer.tar' for x in manifest['layers']]
    archive = folder / 'docker-image.tar'
    with tarfile.open(archive, 'w') as tar:
        tar.add(CACHE / manifest['config']['digest'].split(':')[1], arcname=config_name)
        for item, name in zip(manifest['layers'], layer_names):
            blob = CACHE / item['digest'].split(':')[1]
            layer = folder / 'current-layer.tar'
            opener = gzip.open if 'gzip' in item['mediaType'] else open
            with opener(blob, 'rb') as src, layer.open('wb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
            tar.add(layer, arcname=name)
            layer.unlink()  # Only our temporary decompressed transfer layer.
        data = json.dumps([dict(Config=config_name, RepoTags=[args.image], Layers=layer_names)]).encode()
        info = tarfile.TarInfo('manifest.json')
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    subprocess.run(['docker','load','-i',str(archive)], check=True)
    actual = json.loads(subprocess.check_output(['docker','image','inspect',args.image],text=True))[0]
    expected_config = json.loads((CACHE / manifest['config']['digest'].split(':')[1]).read_text())
    # Docker 29/containerd exposes a converted manifest digest as Id, not
    # necessarily the config digest. Validate runnable content, not that alias.
    assert actual['RootFS']['Layers'] == expected_config['rootfs']['diff_ids']
    assert actual['Architecture'] == expected_config['architecture']
    assert actual['Os'] == expected_config['os']
    for key in ('Env','Cmd','Entrypoint','WorkingDir','User','Labels','Volumes','ExposedPorts','Healthcheck','StopSignal'):
        assert (actual['Config'].get(key) or None) == (expected_config['config'].get(key) or None), key
    (folder / 'import-verification.json').write_text(json.dumps(dict(
        imported_id=actual['Id'], original_config_digest=manifest['config']['digest'],
        rootfs_diff_ids_matched=True, runtime_config_matched=True,
        rootfs=actual['RootFS']), indent=2))
    print('IMPORTED_ROOTFS_AND_RUNTIME_CONFIG_VERIFIED=' + actual['Id'], flush=True)
    archive.unlink()  # Keep verified compressed cache/provenance, not duplicate transport tar.


if __name__ == '__main__':
    main()
