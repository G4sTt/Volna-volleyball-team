"""Import lab images into the local Docker Desktop node.

Uses a temporary Pod with the node's containerd socket and read-only ctr binary.
Requires cluster-admin privileges. Only for the local single-node learning cluster.
"""
from pathlib import Path
import importlib.util
import json
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('clusterbuild', ROOT / 'scripts/build-in-cluster.py')
cb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cb)

IMPORT_CODE = r'''
import hashlib, json, pathlib, shutil, subprocess, sys, tarfile, urllib.request
registry='http://registry.volleyball-build.svc.cluster.local:5000/v2/'
images=json.loads(sys.argv[1])
work=pathlib.Path('/tmp/image-import')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
existing=set(subprocess.check_output(['/node-bin/ctr','--address','/run/containerd/containerd.sock','--namespace','k8s.io','images','list','-q'],text=True).splitlines())
for ref in images:
    if ref in existing:
        print('CACHED '+ref,flush=True)
        continue
    repo_tag=ref.split('/',1)[1]
    repo,tag=repo_tag.rsplit(':',1)
    url=registry+repo+'/manifests/'+tag
    headers={'Accept':'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'}
    with opener.open(urllib.request.Request(url,headers=headers),timeout=60) as r:
        manifest_bytes=r.read()
        media=r.headers['Content-Type'].split(';')[0]
    manifest=json.loads(manifest_bytes)
    digest='sha256:'+hashlib.sha256(manifest_bytes).hexdigest()
    layout=work/repo
    blobs=layout/'blobs'/'sha256'
    blobs.mkdir(parents=True,exist_ok=True)
    (blobs/digest.split(':')[1]).write_bytes(manifest_bytes)
    for desc in [manifest['config']]+manifest['layers']:
        target=blobs/desc['digest'].split(':')[1]
        h=hashlib.sha256()
        with opener.open(registry+repo+'/blobs/'+desc['digest'],timeout=120) as src, target.open('wb') as dst:
            while True:
                chunk=src.read(1024*1024)
                if not chunk:break
                dst.write(chunk); h.update(chunk)
        if 'sha256:'+h.hexdigest()!=desc['digest']:raise RuntimeError('Blob checksum mismatch')
    (layout/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
    index={'schemaVersion':2,'manifests':[{'mediaType':media,'digest':digest,'size':len(manifest_bytes),
      'annotations':{'io.containerd.image.name':ref,'org.opencontainers.image.ref.name':ref}}]}
    (layout/'index.json').write_text(json.dumps(index))
    archive=work/(repo+'.tar')
    with tarfile.open(archive,'w') as output:
        for item in layout.iterdir():output.add(item,arcname=item.name)
    subprocess.run(['/node-bin/ctr','--address','/run/containerd/containerd.sock','--namespace','k8s.io','images','import',str(archive)],check=True)
    print('IMPORTED '+ref,flush=True)
    shutil.rmtree(layout); archive.unlink()
'''


def main():
    info=json.loads((cb.RUN/'build.json').read_text(encoding='utf-8-sig'))
    images=list(info['images'].values())
    if not images:raise RuntimeError('No built images')
    nodes=json.loads(cb.kube('get','nodes','-o','json').stdout)['items']
    if len(nodes)!=1:raise RuntimeError('This importer supports a single-node local cluster only.')
    node=nodes[0]['metadata']['name']
    pod='image-import-'+uuid.uuid4().hex[:8]
    config='image-import-code-'+uuid.uuid4().hex[:8]
    cb.apply([
      {'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':config,'namespace':cb.NS},'data':{'import.py':IMPORT_CODE}},
      {'apiVersion':'v1','kind':'Pod','metadata':{'name':pod,'namespace':cb.NS},'spec':{
        'nodeName':node,'restartPolicy':'Never','automountServiceAccountToken':False,
        'containers':[{'name':'import','image':'python:3.12-slim','command':['python','/code/import.py',json.dumps(images)],
          'volumeMounts':[{'name':'code','mountPath':'/code','readOnly':True},
             {'name':'ctr','mountPath':'/node-bin','readOnly':True},{'name':'socket','mountPath':'/run/containerd/containerd.sock'}],
          'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'512Mi'}}}],
        'volumes':[{'name':'code','configMap':{'name':config}},
          {'name':'ctr','hostPath':{'path':'/usr/local/bin','type':'Directory'}},
          {'name':'socket','hostPath':{'path':'/run/containerd/containerd.sock','type':'Socket'}}]}}
    ])
    deadline=time.monotonic()+600
    while time.monotonic()<deadline:
        status=json.loads(cb.kube('-n',cb.NS,'get','pod',pod,'-o','json').stdout)['status']
        if status.get('phase') in ('Succeeded','Failed'):
            print(cb.kube('-n',cb.NS,'logs',pod).stdout,flush=True)
            if status['phase']!='Succeeded':raise RuntimeError(f'Import failed: {pod}')
            cb.kube('-n',cb.NS,'delete','pod',pod,'--wait=false')
            cb.kube('-n',cb.NS,'delete','configmap',config)
            return
        time.sleep(2)
    raise RuntimeError(f'Import timed out: {pod}')


if __name__=='__main__':main()
