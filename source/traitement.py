"""Traitement de la voix à l'export : réduction du bruit, égaliseur, nivellement, normalisation et limiteur.

Tout est traité par morceaux (fichiers temporaires projetés en mémoire) pour supporter des heures d'audio
sur un Mac de 8 Go. Les gains (nivellement, normalisation, limiteur) sont calculés sur des blocs de 10 ms
puis interpolés à l'échantillon.
"""
import os
import subprocess
import wave

import numpy as np

SR_RNNOISE = 48000
MORCEAU = 1 << 20                 # échantillons traités à la fois (≈ 22 s à 48 kHz)


# ------------------------------------------------------------------ lecture / écriture

def lire_wav(chemin):
    """Renvoie (memmap float32 [n, canaux] à l'échelle ±1, fréquence) — le fichier est converti une fois."""
    with wave.open(chemin, 'rb') as w:
        sr, nc, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        sortie = np.lib.format.open_memmap(chemin + '.f32.npy', mode='w+', dtype=np.float32, shape=(n, nc))
        fait = 0
        while fait < n:
            brut = np.frombuffer(w.readframes(min(MORCEAU, n - fait)), dtype=np.uint8)
            if sw == 3:
                o = brut.reshape(-1, 3).astype(np.int32)
                v = o[:, 0] | (o[:, 1] << 8) | (o[:, 2] << 16)
                v = (np.where(v & 0x800000, v - 0x1000000, v) / 8388608.0)
            else:
                v = np.frombuffer(brut.tobytes(), dtype='<i2') / 32768.0
            v = v.reshape(-1, nc).astype(np.float32)
            sortie[fait:fait + len(v)] = v
            fait += len(v)
    return sortie, sr


def ecrire_wav(chemin, signal, sr, bits=16, gain=None):
    """Écrit un signal float [n, canaux] en WAV 16 ou 24 bits, en appliquant `gain(i0, i1)` si fourni."""
    n, nc = signal.shape
    with wave.open(chemin, 'wb') as w:
        w.setnchannels(nc)
        w.setsampwidth(bits // 8)
        w.setframerate(sr)
        for i0 in range(0, n, MORCEAU):
            i1 = min(n, i0 + MORCEAU)
            v = np.asarray(signal[i0:i1], dtype=np.float64)
            if gain is not None:
                v = v * gain(i0, i1)[:, None]
            v = np.clip(v, -1.0, 1.0)
            if bits == 24:
                q = np.round(v * 8388607).astype(np.int32).reshape(-1)
                o = np.empty((len(q), 3), dtype=np.uint8)
                o[:, 0], o[:, 1], o[:, 2] = q & 0xFF, (q >> 8) & 0xFF, (q >> 16) & 0xFF
                w.writeframes(o.tobytes())
            else:
                w.writeframes(np.round(v * 32767).astype('<i2').tobytes())


# ------------------------------------------------------------------------ filtres

def fir(sr, gain_db, n=4096):
    """Filtre à phase linéaire dont la réponse suit `gain_db(f)` (dB), par échantillonnage en fréquence."""
    f = np.fft.rfftfreq(n, 1 / sr)
    mag = 10 ** (gain_db(np.maximum(f, 1.0)) / 20)
    h = np.roll(np.fft.irfft(mag, n), n // 2) * np.hanning(n)
    return h


def reponse_voix(f):
    """Égaliseur voix : coupe-bas 80 Hz (12 dB/oct), léger creux à 250 Hz, présence +3 dB vers 3,5 kHz."""
    db = np.where(f < 80, np.maximum(-60, 12 * np.log2(f / 80)), 0.0)
    db += -1.5 * np.exp(-np.log2(f / 250) ** 2 / (2 * 0.6 ** 2))
    db += 3.0 * np.exp(-np.log2(f / 3500) ** 2 / (2 * 0.7 ** 2))
    return db


def reponse_k(sr):
    """Pondération K de la norme ITU-R BS.1770 (préfiltre en plateau + passe-haut), pour mesurer les LUFS."""
    def biquad(b, a, f):
        z = np.exp(-2j * np.pi * f / sr)
        return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z))
    # coefficients calculés pour la fréquence `sr` (méthode de la norme, comme pyloudnorm)
    G, Q, fc = 4.0, 1 / np.sqrt(2), 1500.0
    K = np.tan(np.pi * fc / sr); Vh = 10 ** (G / 20); Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = [(Vh + Vb * K / Q + K * K) / a0, 2 * (K * K - Vh) / a0, (Vh - Vb * K / Q + K * K) / a0]
    a1 = [1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0]
    fc2, Q2 = 38.13547087602444, 0.5003270373238773
    K2 = np.tan(np.pi * fc2 / sr)
    b2 = [1, -2, 1]
    a2 = [1, 2 * (K2 * K2 - 1) / (1 + K2 / Q2 + K2 * K2), (1 - K2 / Q2 + K2 * K2) / (1 + K2 / Q2 + K2 * K2)]
    return lambda f: 20 * np.log10(np.maximum(biquad(b1, a1, f) * biquad(b2, a2, f), 1e-6))


class Convolution:
    """Filtrage par blocs (overlap-add), compensation du retard de phase linéaire comprise."""

    def __init__(self, h, nc):
        self.h, self.m = h, len(h)
        self.reste = np.zeros((self.m - 1, nc))
        self.a_jeter = self.m // 2          # retard du filtre à phase linéaire

    def traiter(self, x, dernier=False):
        if dernier:
            x = np.vstack([x, np.zeros((self.m // 2, x.shape[1]))])
        n = len(x)
        nfft = 1 << int(np.ceil(np.log2(n + self.m - 1)))
        H = np.fft.rfft(self.h, nfft)
        y = np.fft.irfft(np.fft.rfft(x, nfft, axis=0) * H[:, None], nfft, axis=0)[:n + self.m - 1]
        y[:self.m - 1] += self.reste
        self.reste = y[n:].copy()
        y = y[:n]
        if self.a_jeter:
            k = min(self.a_jeter, len(y))
            y, self.a_jeter = y[k:], self.a_jeter - k
        return y


# --------------------------------------------------------------- réduction du bruit

def reduire_bruit(signal, sr, rnnoise, dossier, force, progression=None):
    """RNNoise (48 kHz, 16 bits, par canal) ; `force` = part du signal débruité (0–1). Renvoie (signal, sr)."""
    n, nc = signal.shape
    sortie = np.lib.format.open_memmap(os.path.join(dossier, 'debruite.f32.npy'), mode='w+', dtype=np.float32,
                                       shape=(int(np.ceil(n * SR_RNNOISE / sr)) if sr != SR_RNNOISE else n, nc))
    for c in range(nc):
        brut = os.path.join(dossier, f'canal{c}.raw')
        if sr == SR_RNNOISE:
            with open(brut, 'wb') as f:
                for i0 in range(0, n, MORCEAU):
                    f.write(np.round(np.clip(signal[i0:i0 + MORCEAU, c], -1, 1) * 32767).astype('<i2').tobytes())
        else:                            # rééchantillonnage par Core Audio
            tmp = os.path.join(dossier, f'canal{c}.wav')
            ecrire_wav(tmp, signal[:, c:c + 1], sr, 16)
            tmp48 = os.path.join(dossier, f'canal{c}_48k.wav')
            subprocess.run(['afconvert', '-f', 'WAVE', '-d', 'LEI16@48000', tmp, tmp48], check=True, capture_output=True)
            with wave.open(tmp48) as w, open(brut, 'wb') as f:
                while d := w.readframes(MORCEAU):
                    f.write(d)
            os.remove(tmp); os.remove(tmp48)
        # RNNoise travaille par trames de 480 : compléter avec du silence
        taille = os.path.getsize(brut) // 2
        with open(brut, 'ab') as f:
            f.write(b'\0\0' * ((-taille) % 480 + 480))
        debruite = brut + '.out'
        subprocess.run([rnnoise, brut, debruite], check=True, capture_output=True)
        avant = np.memmap(brut, dtype='<i2', mode='r')
        apres = np.memmap(debruite, dtype='<i2', mode='r')
        m = len(sortie)
        for i0 in range(0, m, MORCEAU):
            i1 = min(m, i0 + MORCEAU)
            sortie[i0:i1, c] = (force * apres[i0:i1] + (1 - force) * avant[i0:i1]) / 32768.0
        del avant, apres
        os.remove(brut); os.remove(debruite)
        if progression:
            progression((c + 1) / nc)
    return sortie, SR_RNNOISE


# ------------------------------------------------------------------- gains par bloc

def moyenne_glissante(v, rayon):
    if rayon <= 0:
        return v
    k = 2 * rayon + 1
    p = np.concatenate([np.full(rayon, v[0]), v, np.full(rayon, v[-1])])
    c = np.cumsum(np.concatenate([[0.0], p]))
    return (c[k:] - c[:-k]) / k


def minimum_glissant(v, rayon):
    p = np.concatenate([np.full(rayon, v[0]), v, np.full(rayon, v[-1])])
    return np.lib.stride_tricks.sliding_window_view(p, 2 * rayon + 1).min(axis=1)


def maximum_glissant(v, rayon):
    p = np.concatenate([np.full(rayon, v[0]), v, np.full(rayon, v[-1])])
    return np.lib.stride_tricks.sliding_window_view(p, 2 * rayon + 1).max(axis=1)


def lufs_integre(ms_k, gain_db, bloc_s):
    """Sonie intégrée (BS.1770) à partir de l'énergie pondérée K par bloc, après application du gain."""
    e = ms_k * 10 ** (gain_db / 10)
    pas = max(1, int(round(0.1 / bloc_s)))            # fenêtres de 400 ms, pas de 100 ms
    largeur = 4 * pas
    if len(e) < largeur:
        return -70.0
    c = np.cumsum(np.concatenate([[0.0], e]))
    z = (c[largeur::pas] - c[:-largeur:pas]) / largeur
    l = -0.691 + 10 * np.log10(z + 1e-12)
    z = z[l > -70]
    if not len(z):
        return -70.0
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z.mean())) if len(z) else -70.0


# ------------------------------------------------------------------------ chaîne

def ameliorer(entree, sortie, opts, outils, dossier_tmp, progression=lambda etape, pct: None):
    """Lit `entree` (WAV), applique la chaîne choisie et écrit `sortie` (WAV 16 ou 24 bits).

    opts : bruit ('aucun'|'leger'|'fort'), egaliseur, nivellement (bool), cible_lufs (float|None),
           limiteur (bool), bits (16|24)
    """
    os.makedirs(dossier_tmp, exist_ok=True)
    signal, sr = lire_wav(entree)
    a_effacer = [entree + '.f32.npy']

    if opts.get('bruit', 'aucun') != 'aucun':
        progression('bruit', 0)
        signal, sr = reduire_bruit(signal, sr, outils['rnnoise'], dossier_tmp, 0.65 if opts['bruit'] == 'leger' else 1.0,
                                   lambda p: progression('bruit', p * 100))
        a_effacer.append(os.path.join(dossier_tmp, 'debruite.f32.npy'))

    n, nc = signal.shape
    B = sr // 100                                       # blocs de 10 ms
    nb = int(np.ceil(n / B))
    ms, ms_k, crete = np.zeros(nb), np.zeros(nb), np.zeros(nb)

    # 1. égaliseur (écrit un signal filtré) + mesures par bloc
    eq = np.lib.format.open_memmap(os.path.join(dossier_tmp, 'eq.f32.npy'), mode='w+', dtype=np.float32, shape=(n, nc))
    a_effacer.append(os.path.join(dossier_tmp, 'eq.f32.npy'))
    filtre = Convolution(fir(sr, reponse_voix), nc) if opts.get('egaliseur') else None
    kfiltre = Convolution(fir(sr, reponse_k(sr), 2048), nc)
    ecrit, ecrit_k = 0, 0
    for i0 in range(0, n, MORCEAU):
        dernier = i0 + MORCEAU >= n
        x = np.asarray(signal[i0:i0 + MORCEAU], dtype=np.float64)
        y = filtre.traiter(x, dernier) if filtre else x
        y = y[:n - ecrit]
        eq[ecrit:ecrit + len(y)] = y
        ecrit += len(y)
        yk = kfiltre.traiter(y, dernier and not filtre)[:n - ecrit_k]
        # mesures par bloc sur les échantillons déjà disponibles
        for nom, v, debut in (('y', y, ecrit - len(y)), ('k', yk, ecrit_k)):
            if not len(v):
                continue
            b0, b1 = debut // B, (debut + len(v) - 1) // B
            idx = (np.arange(debut, debut + len(v)) // B)
            s = np.bincount(idx - b0, weights=(v ** 2).sum(1), minlength=b1 - b0 + 1)
            if nom == 'y':
                ms[b0:b1 + 1] += s / B
                np.maximum.at(crete, idx, np.abs(v).max(1))
            else:
                ms_k[b0:b1 + 1] += s / B
        ecrit_k += len(yk)
        progression('traitement', 60 * min(1, ecrit / n))
    if filtre:                                          # vider le filtre K
        yk = kfiltre.traiter(np.zeros((0, nc)), True)[:n - ecrit_k]
        if len(yk):
            idx = np.arange(ecrit_k, ecrit_k + len(yk)) // B
            ms_k += np.bincount(idx, weights=(yk ** 2).sum(1), minlength=nb)[:nb] / B

    # 2. gains par bloc (en dB)
    gain = np.zeros(nb)
    niveau = 10 * np.log10(moyenne_glissante(ms / nc, 20) + 1e-12)          # 400 ms
    plancher = np.percentile(niveau, 10)
    parole = niveau > max(plancher + 9, -60)
    if opts.get('nivellement') and parole.any():
        cible = -20.0
        g = np.clip(cible - niveau, -10, 15)
        # entre les phrases : garder le gain de la parole voisine (pas d'amplification du bruit)
        ip = np.flatnonzero(parole)
        g = np.interp(np.arange(nb), ip, g[ip])
        g = moyenne_glissante(moyenne_glissante(g, 15), 15)                     # lissage ≈ 300 ms
        gain += g
        # compresseur doux : 3:1 au-dessus de −16 dBFS, attaque ≈ 20 ms, relâchement ≈ 150 ms
        rapide = 10 * np.log10(moyenne_glissante(ms / nc, 2) + 1e-12) + gain
        reduc = np.where(rapide > -16, (rapide - -16) * (1 - 1 / 3), 0)
        gain -= moyenne_glissante(maximum_glissant(reduc, 8), 4)
    if opts.get('bruit', 'aucun') != 'aucun':
        # atténuer encore le fond entre les phrases (expandeur), transitions douces
        att = np.where(parole, 0.0, -6.0 if opts['bruit'] == 'leger' else -10.0)
        gain += moyenne_glissante(att, 8)
    def limiteur(g):
        plafond = 10 ** (-1.5 / 20)                     # −1 dBTP avec une marge de 0,5 dB
        besoin = np.minimum(1.0, plafond / (crete * 10 ** (g / 20) + 1e-12))
        return 20 * np.log10(np.maximum(moyenne_glissante(minimum_glissant(besoin, 6), 3), 1e-6))

    cible = opts.get('cible_lufs')
    if cible is not None:
        # normalisation et limiteur s'influencent : on corrige l'écart en quelques passes
        normal = cible - lufs_integre(ms_k, gain, 0.01)
        for _ in range(4):
            g = gain + normal
            g = g + limiteur(g)
            ecart = cible - lufs_integre(ms_k, g, 0.01)
            if abs(ecart) < 0.3:
                break
            normal += min(ecart, 3.0)
        gain = g
    elif opts.get('limiteur'):
        gain = gain + limiteur(gain)

    # 3. application (gain interpolé à l'échantillon) et écriture
    centres = (np.arange(nb) + 0.5) * B
    lin = 10 ** (gain / 20)

    def gain_ech(i0, i1):
        progression('ecriture', 100 * i1 / n)
        return np.interp(np.arange(i0, i1), centres, lin)

    ecrire_wav(sortie, eq, sr, opts.get('bits', 16), gain_ech)
    del signal, eq
    for f in a_effacer:
        if os.path.exists(f):
            os.remove(f)
    return {'sr': sr, 'lufs': lufs_integre(ms_k, gain, 0.01) if opts.get('cible_lufs') is not None else None}
