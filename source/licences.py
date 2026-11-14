"""Génère web/licences.json : composants open source embarqués dans Rature.app et texte de leur licence.

Lancé par construire.sh avec le Python de l'application : python licences.py <dossier whisper.cpp>
"""
import json
import os
import sys
import sysconfig
from importlib import metadata

ICI = os.path.dirname(os.path.abspath(__file__))
WHISPER_CPP = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ICI, '..', 'build', 'whisper.cpp')

MIT_OPENAI = """MIT License

Copyright (c) 2022 OpenAI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

# (distribution Python, rôle dans Rature, adresse)
PAQUETS = [
    ('numpy', {'fr': 'Calcul sur les signaux audio (coupes, export)', 'en': 'Audio signal processing (cuts, export)'}, 'https://numpy.org'),
    ('pywebview', {'fr': 'Fenêtre native de l\'application', 'en': 'Native application window'}, 'https://pywebview.flowrl.com'),
    ('bottle', {'fr': 'Dépendance de pywebview', 'en': 'pywebview dependency'}, 'https://bottlepy.org'),
    ('proxy_tools', {'fr': 'Dépendance de pywebview', 'en': 'pywebview dependency'}, 'https://github.com/jtushman/proxy_tools'),
    ('typing_extensions', {'fr': 'Dépendance de pywebview', 'en': 'pywebview dependency'}, 'https://github.com/python/typing_extensions'),
    ('pyobjc-core', {'fr': 'Pont entre Python et macOS', 'en': 'Bridge between Python and macOS'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('pyobjc-framework-Cocoa', {'fr': 'Accès à AppKit / Foundation', 'en': 'Access to AppKit / Foundation'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('pyobjc-framework-WebKit', {'fr': 'Accès à WebKit', 'en': 'Access to WebKit'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('pyobjc-framework-Quartz', {'fr': 'Accès à Quartz', 'en': 'Access to Quartz'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('pyobjc-framework-Security', {'fr': 'Accès à Security', 'en': 'Access to Security'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('pyobjc-framework-UniformTypeIdentifiers', {'fr': 'Types de fichiers', 'en': 'File types'}, 'https://github.com/ronaldoussoren/pyobjc'),
    ('certifi', {'fr': 'Certificats pour le téléchargement sécurisé des modèles', 'en': 'Certificates for secure model downloads'}, 'https://github.com/certifi/python-certifi'),
    ('mlx', {'fr': 'Calcul sur le GPU des puces Apple (modèle Cohere, téléchargé à la demande)', 'en': 'Apple silicon GPU computing (Cohere model, downloaded on demand)'}, 'https://github.com/ml-explore/mlx'),
    ('mlx-metal', {'fr': 'Noyaux GPU de MLX (téléchargé à la demande)', 'en': 'MLX GPU kernels (downloaded on demand)'}, 'https://github.com/ml-explore/mlx'),
    ('mlx-speech', {'fr': 'Exécution du modèle Cohere Transcribe', 'en': 'Runs the Cohere Transcribe model'}, 'https://github.com/appautomaton/mlx-speech'),
    ('tokenizers', {'fr': 'Découpage du texte pour Cohere', 'en': 'Text tokenization for Cohere'}, 'https://github.com/huggingface/tokenizers'),
    ('safetensors', {'fr': 'Lecture des poids du modèle', 'en': 'Model weight loading'}, 'https://github.com/huggingface/safetensors'),
    ('pyinstaller', {'fr': 'Assemblage de l\'application (chargeur embarqué, exception de la GPL)', 'en': 'Application bundling (embedded bootloader, GPL exception)'}, 'https://pyinstaller.org'),
]


def texte_licence(dist):
    morceaux = []
    for f in dist.files or []:
        nom = os.path.basename(str(f)).upper()
        if any(m in nom for m in ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')) and not str(f).endswith('.py'):
            try:
                morceaux.append(lire(f.locate()))
            except (OSError, FileNotFoundError):
                pass
    return '\n\n'.join(dict.fromkeys(morceaux))


def nom_licence(dist):
    m = dist.metadata
    if m.get('License-Expression'):
        return m['License-Expression']
    lic = (m.get('License') or '').strip()
    if lic and len(lic) < 60 and '\n' not in lic:
        return lic
    for c in m.get_all('Classifier') or []:
        if c.startswith('License :: OSI Approved :: '):
            return c.split(' :: ')[-1].replace(' License', '')
    return 'voir le texte'


def lire(chemin):
    try:
        with open(chemin, encoding='utf-8', errors='replace') as f:
            return f.read().strip()
    except OSError:
        return ''


composants = [
    {'nom': 'whisper.cpp', 'version': '1.8.3', 'licence': 'MIT', 'url': 'https://github.com/ggml-org/whisper.cpp',
     'role': {'fr': 'Moteur de reconnaissance vocale (avec la bibliothèque ggml)', 'en': 'Speech recognition engine (with the ggml library)'}, 'texte': lire(os.path.join(WHISPER_CPP, 'LICENSE'))},
    {'nom': 'Modèles Whisper (OpenAI)', 'version': 'large-v3, large-v3-turbo, small', 'licence': 'MIT',
     'url': 'https://github.com/openai/whisper', 'role': {'fr': 'Poids des modèles de transcription, téléchargés au premier usage', 'en': 'Transcription model weights, downloaded on first use'},
     'texte': MIT_OPENAI},
    {'nom': 'LAME', 'version': '3.100', 'licence': 'LGPL-2.0-or-later', 'url': 'https://lame.sourceforge.io',
     'role': {'fr': 'Encodage MP3 (programme séparé, code source disponible sur le site du projet)',
              'en': 'MP3 encoding (separate program, source code available on the project website)'},
     'texte': lire(os.path.join(WHISPER_CPP, '..', 'lame-3.100', 'COPYING'))},
    {'nom': 'RNNoise', 'version': '0.1.1', 'licence': 'BSD-3-Clause', 'url': 'https://github.com/xiph/rnnoise',
     'role': {'fr': 'Réduction du bruit de fond (Xiph.Org, Mozilla)', 'en': 'Background noise reduction (Xiph.Org, Mozilla)'},
     'texte': lire(os.path.join(WHISPER_CPP, '..', 'rnnoise', 'COPYING'))},
    {'nom': 'FFmpeg', 'version': '9.0.2 (audio)', 'licence': 'LGPL-2.1-or-later', 'url': 'https://ffmpeg.org',
     'role': {'fr': 'Lecture du son des vidéos MKV, AVI, WebM… (programme séparé, compilé sans composant GPL ; code source sur ffmpeg.org)',
              'en': 'Reads the audio of MKV, AVI, WebM… videos (separate program, built without GPL components; source code at ffmpeg.org)'},
     'texte': lire(os.path.join(WHISPER_CPP, '..', 'ffmpeg-9.0.2', 'COPYING.LGPLv2.1'))},
    {'nom': 'Cohere Transcribe 03-2026', 'version': 'int8 MLX', 'licence': 'Apache-2.0', 'url': 'https://huggingface.co/CohereLabs/cohere-transcribe-03-2026',
     'role': {'fr': 'Modèle de transcription facultatif (Cohere Labs), téléchargé à la demande',
              'en': 'Optional transcription model (Cohere Labs), downloaded on demand'},
     'texte': 'Apache License 2.0 — https://www.apache.org/licenses/LICENSE-2.0\nCopyright Cohere Inc. and Cohere Labs.'},
    {'nom': 'Python', 'version': sys.version.split()[0], 'licence': 'PSF-2.0', 'url': 'https://www.python.org',
     'role': {'fr': 'Langage et bibliothèque standard', 'en': 'Language and standard library'},
     'texte': lire(os.path.join(sysconfig.get_paths()['stdlib'], 'LICENSE.txt'))
     or lire(os.path.join(sys.base_prefix, 'lib', f'python{sys.version_info[0]}.{sys.version_info[1]}', 'LICENSE.txt'))},
]
# FFmpeg n'est cité que s'il est réellement embarqué (compilé dans le dossier de build)
if not os.path.exists(os.path.join(WHISPER_CPP, '..', 'ffmpeg-9.0.2', 'ffmpeg')):
    composants = [c for c in composants if c['nom'] != 'FFmpeg']
for paquet, role, url in PAQUETS:
    try:
        d = metadata.distribution(paquet)
    except metadata.PackageNotFoundError:
        continue
    composants.append({'nom': d.metadata['Name'], 'version': d.version, 'licence': nom_licence(d), 'url': url,
                       'role': role, 'texte': texte_licence(d)})

# Textes manquants : les cadres PyObjC partagent la licence de pyobjc-framework-Cocoa
cocoa = next((c['texte'] for c in composants if c['nom'] == 'pyobjc-framework-Cocoa'), '')
for c in composants:
    if not c['texte'] and c['nom'].startswith('pyobjc') and cocoa:
        c['texte'] = cocoa
    elif not c['texte']:
        c['texte'] = (f"Licence {c['licence']}. Le paquet ne fournit pas de fichier de licence ; "
                      f"texte de référence : https://opensource.org/license/mit\nSource : {c['url']}")
    if c['nom'].lower() == 'pyinstaller':
        c['licence'] = 'GPL-2.0 + bootloader exception'

sortie = os.path.join(ICI, 'web', 'licences.json')
with open(sortie, 'w') as f:
    json.dump(composants, f, ensure_ascii=False, indent=1)
for c in composants:
    print(f"  {c['nom']:<42} {c['version']:<12} {c['licence']:<28} {len(c['texte']):>6} car.")
