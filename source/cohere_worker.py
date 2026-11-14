"""Transcription par Cohere Transcribe (MLX), lancée dans un processus séparé par Rature.

Usage : cohere_worker.py <entrée 16 kHz mono .wav> <sortie .json> <langue> <dossier du modèle>
Le son est découpé en morceaux d'au plus 28 s, coupés dans le passage le plus calme ; la sortie est une liste
[{'a': début (s), 'b': fin (s), 'texte': …}]. La progression est écrite sur la sortie standard (« progress = N% »).
"""
import json
import sys
import wave
from pathlib import Path

import numpy as np

SR = 16000
MAX_S, FENETRE_S = 28, 6
LANGUES = {'en', 'fr', 'de', 'es', 'it', 'pt', 'nl', 'pl', 'el', 'ar', 'ja', 'zh', 'ko', 'vi'}


def decouper(x):
    hop = SR // 100
    n = len(x) // hop
    env = np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) if n else np.zeros(0)
    morceaux, a = [], 0
    while a < len(x):
        b = min(len(x), a + MAX_S * SR)
        if b < len(x):
            i0, i1 = (b - FENETRE_S * SR) // hop, b // hop
            b = (i0 + int(np.argmin(env[i0:i1]))) * hop
        morceaux.append((a, b))
        a = b
    return morceaux


def main(args):
    entree, sortie, langue, modele = args
    if langue not in LANGUES:
        raise SystemExit(f'langue non prise en charge par Cohere : {langue}')
    import os
    module = os.environ.get('RATURE_MODULE_COHERE')     # paquets téléchargés à la demande (MLX…)
    if module:
        sys.path.insert(0, module)
    from mlx_speech.generation.cohere_asr import CohereAsrModel
    m = CohereAsrModel.from_dir(Path(modele))
    with wave.open(entree) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
    morceaux = decouper(x)
    resultats = []
    for k, (a, b) in enumerate(morceaux):
        texte = m.transcribe(x[a:b], sample_rate=SR, language=langue).text if b - a > SR // 4 else ''
        resultats.append({'a': a / SR, 'b': b / SR, 'texte': texte})
        print(f'progress = {int(100 * (k + 1) / len(morceaux))}%', flush=True)
    with open(sortie, 'w') as f:
        json.dump(resultats, f, ensure_ascii=False)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
