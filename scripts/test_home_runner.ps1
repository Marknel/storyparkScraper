$ErrorActionPreference='Stop'
# Load functions through the AST: verify without admin, network access, or registration.
Set-StrictMode -Version Latest
$installer=Join-Path $PSScriptRoot 'install_home_runner.ps1'
$parseTokens=$null;$parseErrors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($installer,[ref]$parseTokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'Installer parse failed'}
$names=@('Get-CachedGitHubCredential','Request-PrivateRunnerRegistration','Get-HomeRunnerRegistration','Get-ScraperTaskAccounts')
$definitions=@{}
foreach($definition in $ast.FindAll({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true)){
 if($definition.Name -in $names){$definitions[$definition.Name]=$definition.Extent.Text;Invoke-Expression $definition.Extent.Text}
}
class FixtureHttpException : System.Exception {
 [object]$Response
 FixtureHttpException([int]$Status):base('Synthetic HTTP denial'){$this.Response=[pscustomobject]@{StatusCode=$Status}}
}
$script:HeadersSeen=[Collections.Generic.List[hashtable]]::new()
function Get-CachedGitHubCredential([string]$Repository){return $script:CacheResult}
function Read-Host {
 param([string]$Prompt,[switch]$AsSecureString)
 $script:Prompts++
 return ConvertTo-SecureString 'fixture-fallback-only' -AsPlainText -Force
}
function Invoke-RestMethod {
 param([string]$Uri,[hashtable]$Headers,[string]$Method='Get')
 $script:HeadersSeen.Add($Headers)
 if($Headers.Authorization -eq 'Bearer fixture-cache-only' -and $script:HttpStatus -gt 0){throw [FixtureHttpException]::new($script:HttpStatus)}
 if($Uri.EndsWith('/registration-token')){return [pscustomobject]@{token='fixture-one-time-registration';expires_at='2099-01-01T00:00:00Z'}}
 return [pscustomobject]@{private=(-not $script:PublicRepository);full_name='Marknel/storyparkScraper-deploy'}
}
$results=@()
foreach($case in @(
 @{name='cached-success';cache='fixture-cache-only';status=0;public=$false;prompts=0;throws=$false},
 @{name='cache-absent-fallback';cache=$null;status=0;public=$false;prompts=1;throws=$false},
 @{name='cache-401-fallback';cache='fixture-cache-only';status=401;public=$false;prompts=1;throws=$false},
 @{name='cache-403-fallback';cache='fixture-cache-only';status=403;public=$false;prompts=1;throws=$false},
 @{name='cache-404-fallback';cache='fixture-cache-only';status=404;public=$false;prompts=1;throws=$false},
 @{name='public-repository-rejected';cache='fixture-cache-only';status=0;public=$true;prompts=0;throws=$true},
 @{name='server-failure-not-a-token-prompt';cache='fixture-cache-only';status=500;public=$false;prompts=0;throws=$true}
)){
 $script:CacheResult=$case.cache;$script:HttpStatus=$case.status;$script:PublicRepository=$case.public;$script:Prompts=0
 $script:HeadersSeen.Clear()
 $failed=$false
 try{$response=Get-HomeRunnerRegistration 'Marknel/storyparkScraper-deploy' @{Accept='application/vnd.github+json'}}catch{$failed=$true}
 if($failed -ne $case.throws -or $script:Prompts -ne $case.prompts){throw "Wrong selection for $($case.name)"}
 if(-not $failed -and $response.token -ne 'fixture-one-time-registration'){throw 'Wrong registration fixture response'}
 if(@($script:HeadersSeen|Where-Object{$_.Count -ne 0}).Count){throw 'Request headers retained credential values'}
 $results += @{case=$case.name;passed=$true;secure_prompts=$script:Prompts}
}
Invoke-Expression $definitions['Get-CachedGitHubCredential']
$script:GitFixtureSuccess=$true
function git {
 process {
  if($env:GCM_INTERACTIVE -ne 'never' -or $env:GIT_TERMINAL_PROMPT -ne '0' -or $env:GCM_TRACE_SECRETS -ne '0'){throw 'Cache lookup permits interactivity/tracing'}
  if($script:GitFixtureSuccess){$global:LASTEXITCODE=0;'protocol=https';'password=fixture-cache-only'}
  else{$global:LASTEXITCODE=1}
 }
}
$envNames=@('GCM_INTERACTIVE','GIT_TERMINAL_PROMPT','GCM_GUI_PROMPT','GCM_TRACE','GCM_TRACE_SECRETS','GIT_TRACE','GIT_TRACE_CURL','GIT_CURL_VERBOSE')
$before=@{}
foreach($name in $envNames){$before[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
try{
 $credential=Get-CachedGitHubCredential 'Marknel/storyparkScraper-deploy'
 if($credential -ne 'fixture-cache-only'){throw 'Cached helper did not capture credential'}
 foreach($name in $envNames){if([Environment]::GetEnvironmentVariable($name,'Process') -ne $before[$name]){throw "Environment not restored: $name"}}
 $script:GitFixtureSuccess=$false
 $credential=Get-CachedGitHubCredential 'Marknel/storyparkScraper-deploy'
 if($null -ne $credential){throw 'Missing helper credential not treated as absent'}
 foreach($name in $envNames){if([Environment]::GetEnvironmentVariable($name,'Process') -ne $before[$name]){throw "Failure environment not restored: $name"}}
 $results += @{case='noninteractive-cache-helper-and-env-restoration';passed=$true;secure_prompts=0}
}finally{foreach($name in $envNames){if($null -eq $before[$name]){Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue}else{[Environment]::SetEnvironmentVariable($name,$before[$name],'Process')}}}
$python='C:\VMs\storyparkScraper\.venv\Scripts\python.exe'
$target='C:\VMs\storyparkScraper'
$comAction=[pscustomobject]@{ClassId='fixture-com-handler'}
$execAction=[pscustomobject]@{Execute=$python;Arguments='-m storypark_scraper run'}
$wrapperAction=[pscustomobject]@{Execute='pwsh.exe';Arguments='-NoProfile -File "C:\VMs\storyparkScraper\scripts\run.ps1"'}
$workingAction=[pscustomobject]@{Execute='python.exe';WorkingDirectory=$target}
$unrelatedAction=[pscustomobject]@{Execute='pwsh.exe';Arguments='-File "C:\VMs\storyparkScraper-old\run.ps1"'}
foreach($case in @(
 @{name='COM-handler-skipped';actions=@($comAction);expected=@()},
 @{name='Exec-without-optional-properties';actions=@([pscustomobject]@{Execute=$python});expected=@('MAIN-PC\markc')},
 @{name='mixed-COM-and-Exec';actions=@($comAction,$execAction);expected=@('MAIN-PC\markc')},
 @{name='pwsh-wrapper-target-argument';actions=@($wrapperAction);expected=@('MAIN-PC\markc')},
 @{name='working-directory-match';actions=@($workingAction);expected=@('MAIN-PC\markc')},
 @{name='unrelated-path-prefix-skipped';actions=@($unrelatedAction);expected=@()}
)){
 $task=[pscustomobject]@{Actions=$case.actions;Principal=[pscustomobject]@{UserId='MAIN-PC\markc'}}
 $accounts=@(Get-ScraperTaskAccounts -Tasks @($task) -Python $python -TargetRoot $target)
 if(($accounts -join '|') -ne ($case.expected -join '|')){throw "Wrong task identity selection: $($case.name)"}
 $results += @{case=$case.name;passed=$true}
}
$mixedTasks=@(
 [pscustomobject]@{Actions=@($comAction);Principal=[pscustomobject]@{UserId='OTHER\Com'}},
 [pscustomobject]@{Actions=@($execAction);Principal=[pscustomobject]@{UserId='MAIN-PC\markc'}},
 [pscustomobject]@{Actions=@($wrapperAction);Principal=[pscustomobject]@{UserId='MAIN-PC\markc'}},
 [pscustomobject]@{Actions=@($unrelatedAction);Principal=[pscustomobject]@{UserId='OTHER\User'}}
)
$accounts=@(Get-ScraperTaskAccounts -Tasks $mixedTasks -Python $python -TargetRoot $target | Select-Object -Unique)
if($accounts.Count -ne 1 -or $accounts[0] -ne 'MAIN-PC\markc'){throw 'Mixed task list selected the wrong service account'}
$results += @{case='mixed-task-list-account-selection';passed=$true}
$missingPrincipal=[pscustomobject]@{Actions=@($execAction)}
$rejected=$false
try{Get-ScraperTaskAccounts -Tasks @($missingPrincipal) -Python $python -TargetRoot $target | Out-Null}catch{$rejected=$true}
if(-not $rejected){throw 'Matching task with missing identity was silently accepted'}
$results += @{case='matching-task-missing-identity-rejected';passed=$true}
$results | ConvertTo-Json -Depth 4


