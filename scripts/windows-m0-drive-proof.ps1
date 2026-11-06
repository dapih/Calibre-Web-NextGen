# Exercise the locally repaired Drive authentication dependency chain.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$cache = Join-Path $root '.m0'
$python = Join-Path $cache 'python\python.exe'
$run = Join-Path $cache ('drive-proof-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
$names = @('PATH','PYTHONHOME','PYTHONPATH','PIP_CONFIG_FILE')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PATH = (Join-Path $env:SystemRoot 'System32') + ';' + $env:SystemRoot
    $env:PYTHONHOME = ''; $env:PYTHONPATH = ''; $env:PIP_CONFIG_FILE = 'NUL'
    $venv = Join-Path $run 'runtime'
    & $python -I -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Private environment creation failed.' }
    $testPython = Join-Path $venv 'Scripts\python.exe'
    & $testPython -I -m pip install --no-index --find-links (Join-Path $cache 'drive-repaired') --find-links (Join-Path $cache 'security-wheels') --find-links (Join-Path $cache 'n1-wheelhouse-locked') 'PyDrive2==1.21.3+cwng.m0' *> (Join-Path $run 'install.log')
    if ($LASTEXITCODE -ne 0) { throw "Private Drive install failed; see $run\install.log" }
    & $testPython -I -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Drive dependency metadata is inconsistent.' }
    $probe = @'
import datetime, importlib.metadata as metadata
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from oauth2client import crypt
from oauth2client.service_account import ServiceAccountCredentials
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

assert metadata.version('PyDrive2') == '1.21.3+cwng.m0'
assert metadata.version('oauth2client') == '4.1.3+cwng.m0'
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'M0 fixture')])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()))
cert_pem = cert.public_bytes(serialization.Encoding.PEM)
archive = pkcs12.serialize_key_and_certificates(b'fixture', key, cert, None, serialization.BestAvailableEncryption(b'notasecret'))
for encoded in (pem, archive):
    signer = crypt.Signer.from_string(encoded)
    signature = signer.sign(b'message')
    key.public_key().verify(signature, b'message', padding.PKCS1v15(), hashes.SHA256())
    verifier = crypt.Verifier.from_string(cert_pem, True)
    assert verifier.verify(b'message', signature)
    assert not verifier.verify(b'changed', signature)
assert crypt.Signer.from_string(crypt.pkcs12_key_as_pem(archive, b'notasecret')).sign(b'message')
credentials = ServiceAccountCredentials.from_json_keyfile_dict({
    'type': 'service_account', 'client_email': 'fixture@example.invalid', 'client_id': 'fixture',
    'private_key_id': 'fixture', 'private_key': pem.decode(), 'token_uri': 'https://example.invalid/token',
}, scopes=['fixture'])
assert credentials._generate_assertion().count(b'.') == 2
assert GoogleAuth and GoogleDrive
print('Drive imports, PEM/PKCS12 signing, verification and service-account assertion passed.')
'@
    $probe | & $testPython -I -
    if ($LASTEXITCODE -ne 0) { throw 'Drive cryptography proof failed.' }
    Write-Output "Private Drive evidence: $run"
} finally {
    foreach ($name in $names) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath ('Env:' + $name) -ErrorAction SilentlyContinue }
        else { Set-Item -LiteralPath ('Env:' + $name) -Value $saved[$name] }
    }
}
