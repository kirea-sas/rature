"""Génère module_cohere.json : les paquets Python nécessaires au modèle Cohere, téléchargés à la demande depuis PyPI.

Lancé par construire.sh avec le Python de l'application (versions = celles installées dans l'environnement de build).
Pour chaque paquet : la roue (wheel) compatible macOS 14 / Apple Silicon / Python de l'app, son adresse et son empreinte.
"""
import json
import os
import re
import sys
import urllib.request
from importlib import metadata

PAQUETS = ['mlx', 'mlx-metal', 'mlx-speech', 'safetensors', 'tokenizers']
CP = f'cp{sys.version_info[0]}{sys.version_info[1]}'
MACOS_MAX = (14, 0)


def compatible(nom_fichier):
    m = re.match(r'.+-(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^-]+)\.whl$', nom_fichier)
    if not m:
        return None
    py, abi, plat = m['py'], m['abi'], m['plat']
    if plat == 'any':
        return (0, 0) if py.startswith('py3') else None
    natif = py == CP and abi == CP                       # pas les variantes « t » (Python sans GIL)
    stable = abi == 'abi3' and py in ('cp38', 'cp39', 'cp310', 'cp311', 'cp312', CP)
    pur = py.startswith('py3') and abi == 'none'           # code Python pur, mais propre à macOS (ex. mlx-metal)
    if not (natif or stable or pur):
        return None
    p = re.match(r'macosx_(\d+)_(\d+)_(arm64|universal2)$', plat)
    if not p or (int(p[1]), int(p[2])) > MACOS_MAX:
        return None
    return (int(p[1]), int(p[2]))


manifeste = []
for nom in PAQUETS:
    version = metadata.version(nom)
    with urllib.request.urlopen(f'https://pypi.org/pypi/{nom}/{version}/json', timeout=30) as r:
        fichiers = json.load(r)['urls']
    choix = [(compatible(f['filename']), f) for f in fichiers if f['packagetype'] == 'bdist_wheel']
    choix = [c for c in choix if c[0] is not None]
    if not choix:
        raise SystemExit(f'aucune roue compatible pour {nom} {version}')
    _, f = max(choix, key=lambda c: c[0])
    manifeste.append({'nom': nom, 'version': version, 'fichier': f['filename'], 'url': f['url'],
                      'sha256': f['digests']['sha256'], 'taille': f['size']})

sortie = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'module_cohere.json')
with open(sortie, 'w') as f:
    json.dump(manifeste, f, indent=1)
for m in manifeste:
    print(f"  {m['fichier']:<60} {m['taille'] / 1e6:6.1f} Mo")
print(f"  total {sum(m['taille'] for m in manifeste) / 1e6:.1f} Mo")
