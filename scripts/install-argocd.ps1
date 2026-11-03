param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$runDirectory = Join-Path $projectRoot '.run/argocd'
New-Item -ItemType Directory -Path $runDirectory -Force | Out-Null
$version = 'v3.5.7'
$kubectlCommand = Get-Command kubectl -ErrorAction Stop

function Invoke-Kube {
    & $kubectlCommand.Source --context docker-desktop @args
    if ($LASTEXITCODE -ne 0) { throw "kubectl failed: $args" }
}

Invoke-Kube get nodes --request-timeout=10s
Invoke-Kube -n default get secret ghcr-credentials -o name --request-timeout=10s
$namespace = '{"apiVersion":"v1","kind":"Namespace","metadata":{"name":"argocd"}}'
$namespace | & $kubectlCommand.Source --context docker-desktop apply -f -
if ($LASTEXITCODE -ne 0) { throw 'Could not create the argocd namespace.' }

$installFile = Join-Path $runDirectory "install-$version.yaml"
$installUrl = "https://raw.githubusercontent.com/argoproj/argo-cd/$version/manifests/install.yaml"
Invoke-WebRequest -UseBasicParsing -Uri $installUrl -OutFile $installFile
# Fixed release tags can use the node cache. Docker Hub also avoids a flaky
# public.ecr.aws mirror on the local Docker Desktop installation.
$installText = [IO.File]::ReadAllText($installFile)
$installText = $installText.Replace('imagePullPolicy: Always', 'imagePullPolicy: IfNotPresent')
$installText = $installText.Replace('public.ecr.aws/docker/library/redis:', 'docker.io/library/redis:')
[IO.File]::WriteAllText($installFile, $installText, [Text.UTF8Encoding]::new($false))
Invoke-Kube -n argocd apply --server-side -f $installFile
# Redis is a disposable local cache. Recreate prevents an obsolete failing
# image pull from keeping the old single replica alive during installation.
$redisPatch = Join-Path $runDirectory 'redis-strategy.json'
[IO.File]::WriteAllText($redisPatch, '{"spec":{"strategy":{"type":"Recreate","rollingUpdate":null}}}', [Text.UTF8Encoding]::new($false))
Invoke-Kube -n argocd patch deployment argocd-redis --type=merge --patch-file $redisPatch
foreach ($name in @('argocd-redis', 'argocd-repo-server', 'argocd-server', 'argocd-dex-server', 'argocd-applicationset-controller', 'argocd-notifications-controller')) {
    Invoke-Kube -n argocd rollout status "deployment/$name" --timeout=300s
}
Invoke-Kube -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
Invoke-Kube apply -f (Join-Path $projectRoot 'deploy/argocd/project.yaml')
Invoke-Kube apply -f (Join-Path $projectRoot 'deploy/argocd/application.yaml')
Write-Host 'Argo CD installed. The volna Application follows branch deploy, path manifests.'
Write-Host 'After pushing the new CI workflow to dev, wait for its GitOps promotion job.'
Write-Host 'UI: kubectl --context docker-desktop -n argocd port-forward service/argocd-server 8090:443 --address=127.0.0.1'
Write-Host 'Then open https://localhost:8090. Credentials are not printed by this installer.'
