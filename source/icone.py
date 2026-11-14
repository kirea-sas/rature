"""Génère icone.png (1024 px) : forme d'onde blanche sur fond bleu, deux barres « coupées » barrées de rouge."""
import struct
import sys
import zlib

import numpy as np

N, SS = 1024, 3                       # taille finale, sur-échantillonnage pour l'anticrénelage
M = N * SS
y, x = np.mgrid[0:M, 0:M].astype(np.float32) / SS


def rect_arrondi(x0, y0, x1, y1, r):
    """Masque (0/1) d'un rectangle à coins arrondis."""
    cx = np.clip(x, x0 + r, x1 - r)
    cy = np.clip(y, y0 + r, y1 - r)
    return ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)


def peindre(img, masque, couleur, alpha=1.0):
    a = masque.astype(np.float32)[..., None] * alpha
    img[..., :3] = img[..., :3] * (1 - a) + np.array(couleur, np.float32) * a
    img[..., 3:] = img[..., 3:] * (1 - a) + a


img = np.zeros((M, M, 4), np.float32)

# Fond : carré arrondi (grille d'icône macOS : 824 px centrés), dégradé vertical
fond = rect_arrondi(100, 100, 924, 924, 186)
t = ((y - 100) / 824).clip(0, 1)[..., None]
haut, bas = np.array([0.38, 0.47, 1.0]), np.array([0.17, 0.25, 0.80])
grad = haut * (1 - t) + bas * t
a = fond.astype(np.float32)[..., None]
img[..., :3] = grad * a
img[..., 3:] = a

# Forme d'onde : 7 barres
hauteurs = [190, 330, 250, 430, 300, 360, 200]
larg, ecart = 58, 34
total = len(hauteurs) * larg + (len(hauteurs) - 1) * ecart
x0 = (N - total) / 2
for i, h in enumerate(hauteurs):
    bx = x0 + i * (larg + ecart)
    m = rect_arrondi(bx, N / 2 - h / 2, bx + larg, N / 2 + h / 2, larg / 2)
    peindre(img, m, (1, 1, 1), 0.38 if i in (3, 4) else 1.0)

# Trait de coupe rouge en travers des barres 4 et 5
cx0 = x0 + 3 * (larg + ecart) - 30
cx1 = x0 + 4 * (larg + ecart) + larg + 30
peindre(img, rect_arrondi(cx0, N / 2 - 17, cx1, N / 2 + 17, 17), (1.0, 0.36, 0.36))

# Réduction (moyenne des blocs SS×SS) et écriture PNG
img = img.reshape(N, SS, N, SS, 4).mean((1, 3))
rgb = np.where(img[..., 3:] > 0, img[..., :3] / np.maximum(img[..., 3:], 1e-6), 0)   # dé-prémultiplier
out = (np.concatenate([rgb, img[..., 3:]], -1).clip(0, 1) * 255 + 0.5).astype(np.uint8)
brut = b''.join(b'\0' + ligne.tobytes() for ligne in out)


def bloc(typ, data):
    return struct.pack('>I', len(data)) + typ + data + struct.pack('>I', zlib.crc32(typ + data) & 0xffffffff)


png = b'\x89PNG\r\n\x1a\n' + bloc(b'IHDR', struct.pack('>IIBBBBB', N, N, 8, 6, 0, 0, 0)) \
    + bloc(b'IDAT', zlib.compress(brut, 9)) + bloc(b'IEND', b'')
open(sys.argv[1] if len(sys.argv) > 1 else 'icone.png', 'wb').write(png)
