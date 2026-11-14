#!/usr/bin/env python3
"""Rature — montage audio par le texte.

Serveur local (http://127.0.0.1:8765) :
  /                    accueil : liste des projets
  /p/<id>              projet : import et transcription, puis éditeur
Les projets sont rangés dans ../Projets/<id>/.
"""
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import wave
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np

VERSION = '1.1.0'
# dernière version publiée sur GitHub (une release = une mise à jour, rien d'autre à maintenir)
URL_MAJ = os.environ.get('RATURE_URL_MAJ', 'https://api.github.com/repos/kirea-sas/rature/releases/latest')
# Dans l'application, les fichiers embarqués (web/, bin/) sont dans sys._MEIPASS
RESSOURCES = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(RESSOURCES, 'web')
DONNEES = os.environ.get('RATURE_DONNEES', os.path.expanduser('~/Rature'))
# RATURE_DONNEES / RATURE_SUPPORT permettent de lancer une instance isolée (démo, tests) sans toucher aux vrais projets
SUPPORT = os.environ.get('RATURE_SUPPORT', os.path.expanduser('~/Library/Application Support/Rature'))
PORT = 8765

PAUSE_MIN = 2.0        # un silence plus long devient un bloc « pause » coupable
FONDU = 0.006          # fondu aux raccords (s)
PAS_ENV = 0.010        # pas de l'enveloppe d'énergie (s)
N_CRETE = 4000         # résolution de la forme d'onde d'aperçu
EXT_AUDIO = ('.wav', '.wave', '.bwf', '.mp3', '.m4a', '.aac', '.flac', '.aif', '.aiff', '.aifc', '.caf', '.mp4', '.m4b', '.amr', '.3gp',
             '.mov', '.m4v', '.qt',
             # lus par FFmpeg (macOS ne les ouvre pas) : MKV, AVI, WebM…
             '.mkv', '.webm', '.avi', '.wmv', '.flv', '.ts', '.mts', '.m2ts', '.mpg', '.mpeg', '.vob', '.ogg', '.opus', '.wma')
EXT_VIDEO = ('.mov', '.mp4', '.m4v', '.qt', '.3gp',      # le son est lu directement dans la vidéo par Core Audio
             '.mkv', '.webm', '.avi', '.wmv', '.flv', '.ts', '.mts', '.m2ts', '.mpg', '.mpeg', '.vob')
LANGUES_ISO = {'fre': 'fr', 'fra': 'fr', 'eng': 'en', 'spa': 'es', 'ita': 'it', 'ger': 'de', 'deu': 'de',
               'por': 'pt', 'dut': 'nl', 'nld': 'nl'}
PONCT_SEULE = re.compile(r'^[\s\.\,\?\!\…\:\;\-–—«»"\']+$')

verrou = threading.RLock()
file_travaux = queue.Queue()


# -------------------------------------------------------------------- langue

MESSAGES = {
    'fr': {
        'id_invalide': 'Identifiant de projet invalide', 'sans_titre': 'Sans titre',
        'format_mac': '{nom} : format audio non pris en charge par macOS', 'format_inconnu': '{nom} : format non reconnu',
        'deja_lance': 'La transcription est déjà lancée : créez un nouveau projet pour ajouter des fichiers.',
        'deja_lance_court': 'Transcription déjà lancée.', 'envoi_interrompu': 'Envoi interrompu',
        'choisir_fichiers': 'Choisir les fichiers audio du projet', 'choisir_dossier': 'Choisir un dossier',
        'existe_deja': '« {nom} » existe déjà dans {dossier}', 'attendre': 'Attendez la fin de la transcription ou du téléchargement en cours.',
        'ecriture': 'Impossible d\'écrire dans {dossier}', 'modele_inconnu': 'Modèle inconnu', 'reglage_inconnu': 'Réglage inconnu',
        'espace': 'espace disque insuffisant ({go} Go libres)', 'incomplet': 'fichier incomplet', 'dl_impossible': 'Téléchargement impossible : {e}',
        'hors_dossier': 'Ce modèle est rangé hors du dossier des modèles de Rature : supprimez-le depuis le Finder.',
        'conversion_impossible': 'Conversion impossible : {e}', 'aucun_fichier': 'Aucun fichier à transcrire.',
        'attente_modele': 'En attente du téléchargement du modèle {nom}', 'modele_absent': 'Le modèle {nom} n\'est pas installé (voir les Réglages).',
        'whisper_absent': 'whisper-cli introuvable.', 'fichier_introuvable': 'Fichier introuvable : {chemin}',
        'conversion': 'Conversion de {nom}', 'assemblage': 'Assemblage de {nom}', 'apercu': 'Création de l\'aperçu audio', 'extraction': 'Extraction du son de {nom}',
        'restant': 'environ {n} min restantes', 'transcription_echec': 'La transcription a échoué (voir transcription.log).',
        'ajouter_fichier': 'Ajoutez au moins un fichier audio.', 'tout_coupe': 'Tout est coupé : rien à exporter.',
        'chemin_hors_projet': 'Chemin hors du projet',
        'csv': 'n;montage_debut;montage_fin;timeline_debut;timeline_fin;fichier_source;source_debut;source_fin',
        'menu_reglages': 'Réglages…', 'menu_licences': 'Licences open source', 'fichiers_audio': 'Fichiers audio et vidéo', 'video': 'Vidéo',
        'cohere_sans_whisper': 'Cohere a aussi besoin d\'un modèle Whisper pour placer les mots dans le temps : installez-en un dans les Réglages.',
        'passe_cohere': 'Transcription par Cohere',
        'tous_fichiers': 'Tous les fichiers', 'fichier_forts': 'moments_forts', 'fichier_video': 'video', 'pas_transcrit': 'Le projet n\'est pas encore transcrit.', 'pistes': '{n} pistes audio', 'fichier_coupes': 'coupes', 'coupe': 'coupé', 'stereo': 'stéréo', 'canaux': '{n} canaux',
    },
    'en': {
        'id_invalide': 'Invalid project identifier', 'sans_titre': 'Untitled',
        'format_mac': '{nom}: audio format not supported by macOS', 'format_inconnu': '{nom}: unrecognised format',
        'deja_lance': 'Transcription has already started: create a new project to add files.',
        'deja_lance_court': 'Transcription already started.', 'envoi_interrompu': 'Upload interrupted',
        'choisir_fichiers': 'Choose the project\'s audio files', 'choisir_dossier': 'Choose a folder',
        'existe_deja': '“{nom}” already exists in {dossier}', 'attendre': 'Wait for the current transcription or download to finish.',
        'ecriture': 'Cannot write to {dossier}', 'modele_inconnu': 'Unknown model', 'reglage_inconnu': 'Unknown setting',
        'espace': 'not enough disk space ({go} GB free)', 'incomplet': 'incomplete file', 'dl_impossible': 'Download failed: {e}',
        'hors_dossier': 'This model is stored outside Rature\'s models folder: delete it from the Finder.',
        'conversion_impossible': 'Conversion failed: {e}', 'aucun_fichier': 'No file to transcribe.',
        'attente_modele': 'Waiting for the {nom} model to download', 'modele_absent': 'The {nom} model is not installed (see Settings).',
        'whisper_absent': 'whisper-cli not found.', 'fichier_introuvable': 'File not found: {chemin}',
        'conversion': 'Converting {nom}', 'assemblage': 'Joining {nom}', 'apercu': 'Creating the audio preview', 'extraction': 'Extracting the sound of {nom}',
        'restant': 'about {n} min left', 'transcription_echec': 'Transcription failed (see transcription.log).',
        'ajouter_fichier': 'Add at least one audio file.', 'tout_coupe': 'Everything is cut: nothing to export.',
        'chemin_hors_projet': 'Path outside the project',
        'csv': 'n;edit_start;edit_end;timeline_start;timeline_end;source_file;source_start;source_end',
        'menu_reglages': 'Settings…', 'menu_licences': 'Open source licenses', 'fichiers_audio': 'Audio and video files', 'video': 'Video',
        'cohere_sans_whisper': 'Cohere also needs a Whisper model to place the words in time: install one in Settings.',
        'passe_cohere': 'Transcribing with Cohere',
        'tous_fichiers': 'All files', 'fichier_forts': 'highlights', 'fichier_video': 'video', 'pas_transcrit': 'The project is not transcribed yet.', 'pistes': '{n} audio tracks', 'fichier_coupes': 'cuts', 'coupe': 'cut', 'stereo': 'stereo', 'canaux': '{n} channels',
    },
}
_langue_mac = None


def langue_mac():
    """Langue préférée de macOS : 'fr' si le Mac est en français, sinon 'en'."""
    global _langue_mac
    if _langue_mac is None:
        try:
            sortie = subprocess.run(['defaults', 'read', '-g', 'AppleLanguages'], capture_output=True, text=True, timeout=3).stdout
            m = re.search(r'([a-z]{2})', sortie)
            _langue_mac = 'fr' if m and m.group(1) == 'fr' else 'en'
        except Exception:  # noqa: BLE001
            _langue_mac = 'en'
    return _langue_mac


def langue_ui():
    choix = reglages().get('langue_ui', 'auto')
    return choix if choix in MESSAGES else langue_mac()


def _(cle, **kw):
    return MESSAGES[langue_ui()].get(cle, MESSAGES['fr'][cle]).format(**kw)


# ------------------------------------------------------------------ projets

def dossier(pid):
    if not re.fullmatch(r'[a-z0-9\-]+', pid or ''):
        raise ValueError(_('id_invalide'))
    return os.path.join(projets_dir(), pid)


def chemin(pid, *noms):
    return os.path.join(dossier(pid), *noms)


def resoudre(pid, c):
    """Chemin d'un fichier rangé dans le projet, même si le dossier des projets a été déplacé depuis."""
    if c and not os.path.exists(c) and f'/{pid}/' in c:
        candidat = os.path.join(dossier(pid), c.split(f'/{pid}/', 1)[1])
        if os.path.exists(candidat):
            return candidat
    return c


def charger(pid):
    with open(chemin(pid, 'projet.json')) as f:
        return json.load(f)


def enregistrer(p):
    with verrou:
        p['modifie'] = datetime.now().isoformat(timespec='seconds')
        tmp = chemin(p['id'], 'projet.json.tmp')
        with open(tmp, 'w') as f:
            json.dump(p, f, ensure_ascii=False, indent=1)
        os.replace(tmp, chemin(p['id'], 'projet.json'))


def maj(pid, **champs):
    with verrou:
        p = charger(pid)
        p.update(champs)
        enregistrer(p)
    return p


def slug(txt):
    txt = unicodedata.normalize('NFKD', txt).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', txt).strip('-')[:40] or 'projet'


def creer(nom, genre='montage'):
    """genre : « montage » (monter l'audio par le texte) ou « sous-titres » (une vidéo → un fichier SRT)."""
    pid = f'{slug(nom)}-{secrets.token_hex(2)}'
    os.makedirs(chemin(pid, 'sources'))
    p = {'id': pid, 'nom': nom.strip() or _('sans_titre'), 'cree': datetime.now().isoformat(timespec='seconds'), 'genre': genre,
         'langue': langue_ui(), 'vocabulaire': '', 'verbatim': False, 'sources': [], 'statut': 'brouillon',
         'etape': None, 'progression': 0, 'message': '', 'duree': 0}
    enregistrer(p)
    return p


def avancement(pid, reprise):
    """Où en est le montage : position du repère « là où vous vous étiez arrêté », en part du texte (comme le
    pourcentage de la mini-carte) et en part de la durée. Mis en cache, mots.json pouvant peser plusieurs Mo."""
    f_mots = chemin(pid, 'mots.json')
    if reprise is None or not os.path.exists(f_mots):
        return None
    cle = [reprise, os.path.getmtime(f_mots)]
    f = chemin(pid, 'avancement.json')
    try:
        c = json.load(open(f))
        if c.get('cle') == cle:
            return c['avancement']
    except (OSError, ValueError):
        pass
    jetons = json.load(open(f_mots))['jetons']
    k = next((n for n, j in enumerate(jetons) if j['i'] == reprise), None)
    res = None
    if k is not None and jetons:
        fin = jetons[-1].get('t1') or jetons[-1]['d']
        res = {'texte': round(k / max(1, len(jetons) - 1), 4), 'temps': round(jetons[k]['d'] / max(fin, 1e-6), 4)}
    with open(f, 'w') as sortie:
        json.dump({'cle': cle, 'avancement': res}, sortie)
    return res


def resume(p):
    """Infos pour la carte d'accueil."""
    pid = p['id']
    etat = {}
    if os.path.exists(chemin(pid, 'montage_etat.json')):
        etat = json.load(open(chemin(pid, 'montage_etat.json')))
    exports = (sorted(f for f in os.listdir(chemin(pid, 'exports')) if f.endswith('.wav'))
               if os.path.isdir(chemin(pid, 'exports')) else []) + p.get('exports', [])
    return {**{k: p.get(k) for k in ('id', 'nom', 'cree', 'modifie', 'statut', 'etape', 'progression', 'message', 'duree', 'langue', 'srt', 'langue_detectee')},
            'genre': p.get('genre') or 'montage',
            'n_sources': len(p['sources']),
            'duree_sources': sum(s.get('duree') or 0 for s in p['sources']),
            'duree_montage': etat.get('duree_montage'),
            'n_coupes': len(etat.get('supprimes', [])),
            'avancement': avancement(pid, etat.get('reprise')) if p.get('statut') == 'pret' else None,
            'n_exports': len(exports)}


def lister():
    racine = projets_dir()
    os.makedirs(racine, exist_ok=True)
    res = []
    for pid in os.listdir(racine):
        if os.path.exists(os.path.join(racine, pid, 'projet.json')):
            try:
                res.append(resume(charger(pid)))
            except Exception as e:  # noqa: BLE001
                print('Projet illisible', pid, e, file=sys.stderr)
    return sorted(res, key=lambda r: r.get('modifie') or '', reverse=True)


def supprimer(pid):
    """Envoie le dossier du projet à la corbeille (les fichiers référencés ailleurs ne sont pas touchés)."""
    d = dossier(pid)
    dest = os.path.expanduser(f'~/.Trash/{pid}-{datetime.now():%H%M%S}')
    shutil.move(d, dest)


# ------------------------------------------------------------------ sources

def heure_enregistrement(fichier):
    """Heure d'enregistrement : d'après le nom (ex. DJI_04_20260905_082337) sinon la date du fichier."""
    nom = os.path.basename(fichier)
    m = re.search(r'(20\d{6})[_\-T ]?(\d{6})', nom)
    if m:
        try:
            return datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S').isoformat()
        except ValueError:
            pass
    return None


def date_quicktime(fichier):
    """Date de création inscrite dans une vidéo MP4/MOV (atome moov/mvhd, en UTC), en heure locale ISO."""
    import struct
    try:
        with open(fichier, 'rb') as f:
            fin, pos = os.path.getsize(fichier), 0
            while pos + 8 <= fin:                         # atomes de premier niveau, jusqu'à « moov »
                f.seek(pos)
                taille, typ = struct.unpack('>I4s', f.read(8))
                entete = 8
                if taille == 1:
                    taille, entete = struct.unpack('>Q', f.read(8))[0], 16
                elif taille == 0:
                    taille = fin - pos
                if typ == b'moov':
                    debut = pos + entete
                    while debut + 8 <= pos + taille:
                        f.seek(debut)
                        t2, ty2 = struct.unpack('>I4s', f.read(8))
                        if ty2 == b'mvhd':
                            version = f.read(4)[0]
                            secondes = struct.unpack('>Q' if version == 1 else '>I', f.read(8 if version == 1 else 4))[0]
                            if secondes < 86400 * 365 * 70:      # absente ou nulle (avant 1974)
                                return None
                            d = datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secondes)
                            return d.astimezone().replace(tzinfo=None).isoformat()
                        if t2 < 8:
                            return None
                        debut += t2
                    return None
                if taille < 8:
                    return None
                pos += taille
    except (OSError, struct.error, OverflowError, IndexError):
        return None
    return None


def infos_video(fichier):
    """(est une vidéo, date de tournage locale ISO ou None) d'après Spotlight ; l'extension sert de repli."""
    if not fichier.lower().endswith(EXT_VIDEO):
        return False, None
    r = subprocess.run(['mdls', '-name', 'kMDItemPixelHeight', '-name', 'kMDItemContentCreationDate', fichier],
                       capture_output=True, text=True)
    hauteur = re.search(r'kMDItemPixelHeight\s*=\s*(\d+)', r.stdout)
    date = re.search(r'kMDItemContentCreationDate\s*=\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})', r.stdout)
    video = bool(hauteur) or not fichier.lower().endswith(('.mp4', '.3gp')) or '(null)' in r.stdout
    # la date inscrite par la caméra d'abord (Spotlight donne souvent la date du fichier, fausse pour une copie)
    heure = date_quicktime(fichier)
    if not heure and date:
        heure = datetime.strptime(date.group(1), '%Y-%m-%d %H:%M:%S %z').astimezone().replace(tzinfo=None).isoformat()
    return video, heure


def sonder_ffmpeg(fichier):
    """Fichier que macOS ne lit pas (MKV, AVI…) : pistes audio d'après FFmpeg ; None si illisible."""
    ff = outil('ffmpeg')
    if not ff:
        return None
    r = subprocess.run([ff, '-hide_banner', '-nostdin', '-i', fichier], capture_output=True, text=True, errors='replace')
    d = re.search(r'Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?)', r.stderr)
    pistes = []
    for ligne in r.stderr.splitlines():
        m = re.search(r'Stream #0:\d+(?:\[\w+\])?(?:\((\w+)\))?: Audio: (\w+).*?, (\d+) Hz, ([^,]+)', ligne)
        if m:
            disposition = m.group(4).strip()
            canaux = {'mono': 1, 'stereo': 2, '5.1': 6, '5.1(side)': 6, '7.1': 8}
            n = re.match(r'(\d+) channels', disposition)
            ch = int(n.group(1)) if n else canaux.get(disposition, 2)
            pistes.append({'langue': LANGUES_ISO.get(m.group(1) or '', m.group(1)), 'codec': m.group(2), 'sr': int(m.group(3)),
                           'ch': ch, 'disposition': disposition, 'defaut': '(default)' in ligne})
    if not d or not pistes:
        return None
    nom = os.path.basename(fichier)
    piste = next((k for k, p in enumerate(pistes) if p['defaut']), 0)
    p = pistes[piste]
    duree = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))
    video = fichier.lower().endswith(EXT_VIDEO) or 'Video:' in r.stderr
    descr = ' · '.join(x for x in ((_('video') if video else ''), p['codec'].upper(), f"{p['sr'] / 1000:g} kHz", p['disposition'],
                                   _('pistes', n=len(pistes)) if len(pistes) > 1 else '') if x)
    return {'nom': nom, 'chemin': os.path.abspath(fichier), 'duree': duree, 'taille': os.path.getsize(fichier),
            'codec': 'ffmpeg:' + p['codec'], 'sr': p['sr'], 'ch': min(2, p['ch']), 'bits': 0, 'format': descr, 'video': video,
            'via': 'ffmpeg', 'pistes': pistes, 'piste': piste, 'heure': heure_enregistrement(fichier)}


def extraire_son(s, dest_wav, ch, sr):
    """Son d'une source (Core Audio, ou FFmpeg pour MKV, AVI…) vers un WAV 16 bits."""
    if s.get('via') == 'ffmpeg':
        extraire_ffmpeg(s['chemin'], dest_wav, s.get('piste', 0), ch, sr)
    else:
        afconvert('-f', 'WAVE', '-d', f'LEI16@{sr}', '-c', str(ch), *(['--mix'] if s['ch'] > ch else []), s['chemin'], dest_wav)


def assembler_wav(morceaux, dest):
    """Met bout à bout des WAV de même format (le premier est simplement renommé s'il est seul)."""
    if len(morceaux) == 1:
        os.replace(morceaux[0], dest)
        return
    with wave.open(dest, 'wb') as out:
        for k, m in enumerate(morceaux):
            with wave.open(m, 'rb') as w:
                if k == 0:
                    out.setparams(w.getparams())
                while True:
                    brut = w.readframes(1 << 20)
                    if not brut:
                        break
                    out.writeframes(brut)
            os.remove(m)


def son_film(pid, d, srcs):
    """Projet « sous-titres » : le son de chaque source directement en 16 kHz mono ; renvoie (wav16, lectures, durée)."""
    lectures, morceaux = [], []
    for k, s in enumerate(srcs):
        s['chemin'] = resoudre(pid, s['chemin'])
        if not os.path.exists(s['chemin']):
            raise FileNotFoundError(_('fichier_introuvable', chemin=s['chemin']))
        etape(pid, 'preparation', round(90 * k / len(srcs)), _('extraction', nom=s['nom']))
        m = os.path.join(d, f'son{k:02d}.tmp.wav')
        extraire_son(s, m, 1, 16000)
        with wave.open(m, 'rb') as w:
            n = w.getnframes()
        morceaux.append(m)
        lectures.append({'fichier': s['nom'], 'lecture': s['chemin'], 'n': n, 'sr': 16000, 'heure': s.get('heure')})
    t = 0.0
    for L in lectures:
        L['debut'], L['duree'] = t, L['n'] / L['sr']
        t += L['duree']
    json.dump(lectures, open(os.path.join(d, 'sources.json'), 'w'), ensure_ascii=False, indent=1)
    wav16 = os.path.join(d, 'analyse_16k.wav')
    assembler_wav(morceaux, wav16)
    return wav16, lectures, t


def apercu_film(pid):
    """Aperçu d'écoute d'un film, créé seulement quand on ouvre l'éditeur (« Corriger le texte »)."""
    f = chemin(pid, 'apercu.m4a')
    with verrou_apercu:
        if os.path.exists(f):
            return f
        p = charger(pid)
        d = dossier(pid)
        morceaux = []
        for k, s in enumerate(p['sources']):
            s['chemin'] = resoudre(pid, s['chemin'])
            m = os.path.join(d, f'apercu{k:02d}.tmp.wav')
            extraire_son(s, m, 1, 32000)
            morceaux.append(m)
        tmp = os.path.join(d, 'apercu.tmp.wav')
        assembler_wav(morceaux, tmp)
        afconvert('-f', 'm4af', '-d', 'aac', '-b', '64000', tmp, f + '.tmp')
        os.remove(tmp)
        os.replace(f + '.tmp', f)
    return f


verrou_apercu = threading.Lock()


def extraire_ffmpeg(source, dest_wav, piste=0, ch=2, sr=48000):
    """Son d'une piste (FFmpeg) vers un WAV 16 bits ; le 5.1 est réduit en stéréo (dialogues du canal central inclus)."""
    proc = subprocess.Popen([outil('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error', '-y', '-i', source,
                             '-map', f'0:a:{piste}', '-vn', '-ac', str(ch), '-ar', str(sr), '-c:a', 'pcm_s16le', dest_wav],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
    PROCESSUS.add(proc)
    try:
        sortie = proc.communicate()[0]
    finally:
        PROCESSUS.discard(proc)
    if proc.returncode != 0:
        raise RuntimeError(_('conversion_impossible', e=sortie.strip()[-300:]))


def sonder(fichier):
    nom = os.path.basename(fichier)
    sr = ch = bits = 0
    duree, codec = 0.0, '?'
    try:                                   # WAV PCM : lecture directe, exacte
        with wave.open(fichier, 'rb') as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        if sw in (2, 3):
            bits, codec, duree = sw * 8, f'pcm_s{sw * 8}le', n / sr
    except Exception:  # noqa: BLE001
        pass
    if codec == '?':                       # autres formats : Core Audio (afinfo)
        r = subprocess.run(['afinfo', fichier], capture_output=True, text=True)
        m = re.search(r'Data format:\s+(\d+) ch,\s+(\d+) Hz, (\S+)', r.stdout)
        d = re.search(r'estimated duration: ([\d.]+)', r.stdout)
        if r.returncode != 0 or not m or not d:
            ff = sonder_ffmpeg(fichier)    # MKV, AVI, WebM… : FFmpeg
            if not ff:
                raise ValueError(_('format_mac', nom=nom))
            return ff
        ch, sr, codec, duree = int(m.group(1)), int(m.group(2)), m.group(3).strip("'.,"), float(d.group(1))
        b = re.search(r'source bit depth: I(\d+)', r.stdout) or re.search(r'(\d+)-bit', r.stdout)
        bits = int(b.group(1)) if b else 0
    libelle = 'WAV' if codec.startswith('pcm') else 'PCM' if codec.startswith(('Int', 'Float')) else {'lpcm': 'PCM', 'mp3': 'MP3', 'aac': 'AAC', 'flac': 'FLAC', 'alac': 'ALAC'}.get(codec, codec.upper())
    if codec not in ('pcm_s16le', 'pcm_s24le', 'lpcm', 'flac', 'alac'):
        bits = 0
    video, tournage = infos_video(fichier)
    if video:
        libelle = _('video') + ' · ' + libelle
    descr = f'{libelle} · {sr / 1000:g} kHz' + (f' · {bits} bits' if bits else '') \
        + (' · mono' if ch == 1 else f" · {_('stereo')}" if ch == 2 else f" · {_('canaux', n=ch)}")
    return {'nom': nom, 'chemin': os.path.abspath(fichier), 'duree': duree, 'taille': os.path.getsize(fichier),
            'codec': codec, 'sr': sr, 'ch': ch, 'bits': bits, 'format': descr, 'video': video,
            'heure': heure_enregistrement(fichier) or tournage}


def ajouter_sources(pid, fichiers, copie):
    p = charger(pid)
    if p['statut'] not in ('brouillon', 'erreur'):
        raise ValueError(_('deja_lance'))
    erreurs = []
    for f in fichiers:
        try:
            s = sonder(f)
            if copie and s['video']:      # vidéo déposée dans Rature : seul le son est gardé (pas des Go de vidéo)
                son = os.path.splitext(f)[0] + '.m4a'
                if s.get('via') == 'ffmpeg':
                    wav = os.path.splitext(f)[0] + '.son.wav'
                    extraire_ffmpeg(f, wav, s.get('piste', 0), s['ch'], s['sr'])
                    afconvert('-f', 'm4af', '-d', 'aac', '-b', str(min(320000, 128000 * max(1, s['ch']))), wav, son)
                    os.remove(wav)
                    s['via'] = None                       # le son extrait se lit ensuite comme un M4A ordinaire
                else:
                    afconvert('-f', 'm4af', '-d', 'aac', '-b', str(min(320000, 128000 * max(1, s['ch']))), f, son)
                os.remove(f)
                s.update({k: v for k, v in sonder(son).items() if k in ('chemin', 'taille', 'codec', 'sr', 'ch', 'bits')})
            s['copie'] = copie
            p['sources'].append(s)
        except Exception as e:  # noqa: BLE001
            erreurs.append(str(e))
            if copie:
                os.remove(f)
    p['sources'].sort(key=lambda s: (s.get('heure') or '9999', s['nom']))
    enregistrer(p)
    return p, erreurs


# Remplacée par la fenêtre native quand Rature tourne en application (voir mac.py)
CHOISIR_FICHIERS = None


def choisir_dans_finder():
    if CHOISIR_FICHIERS:
        return CHOISIR_FICHIERS()
    script = '''
    tell current application to activate
    set fs to choose file with prompt "''' + _('choisir_fichiers') + '''" with multiple selections allowed
    set out to ""
    repeat with f in fs
        set out to out & POSIX path of f & linefeed
    end repeat
    return out'''
    r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    if r.returncode != 0:
        return []    # annulé
    return [l for l in r.stdout.splitlines() if l.strip()]


# ------------------------------------------------------------ réglages

FICHIER_REGLAGES = os.path.join(SUPPORT, 'reglages.json')
DEFAUTS = {'dossier_projets': os.path.join(DONNEES, 'Projets' if langue_mac() == 'fr' else 'Projects'),
           'dossier_modeles': os.path.join(SUPPORT, 'Models'),
           'modele': 'large-v3-turbo',
           'langue_ui': 'auto', 'langue_films': 'auto',
           'maj_auto': True,
           'export': {}}


def reglages():
    r = dict(DEFAUTS)
    try:
        with open(FICHIER_REGLAGES) as f:
            r.update({k: v for k, v in json.load(f).items() if k in DEFAUTS and (v or v is False)})
    except (OSError, ValueError):
        pass
    if r['modele'] not in MODELES:
        r['modele'] = DEFAUTS['modele']
    return r


def sauver_reglages(**champs):
    with verrou:
        r = reglages()
        r.update(champs)
        os.makedirs(SUPPORT, exist_ok=True)
        with open(FICHIER_REGLAGES + '.tmp', 'w') as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(FICHIER_REGLAGES + '.tmp', FICHIER_REGLAGES)
    return r


def projets_dir():
    return reglages()['dossier_projets']


def espace_libre(chemin_):
    while chemin_ and not os.path.exists(chemin_):
        chemin_ = os.path.dirname(chemin_)
    try:
        return shutil.disk_usage(chemin_ or '/').free
    except OSError:
        return None


def deplacer_contenu(ancien, nouveau, filtre):
    """Déplace les éléments de `ancien` vers `nouveau` (renommage si même disque, copie sinon)."""
    os.makedirs(nouveau, exist_ok=True)
    if not os.path.isdir(ancien) or os.path.realpath(ancien) == os.path.realpath(nouveau):
        return 0
    n = 0
    for nom in os.listdir(ancien):
        if not filtre(os.path.join(ancien, nom)):
            continue
        if os.path.exists(os.path.join(nouveau, nom)):
            raise FileExistsError(_('existe_deja', nom=nom, dossier=nouveau))
        shutil.move(os.path.join(ancien, nom), os.path.join(nouveau, nom))
        n += 1
    return n


def changer_dossier(cle, nouveau, deplacer):
    if en_cours() or any(t['etat'] == 'en_cours' for t in telechargements.values()):
        raise ValueError(_('attendre'))
    nouveau = os.path.abspath(os.path.expanduser(nouveau))
    os.makedirs(nouveau, exist_ok=True)
    if not os.access(nouveau, os.W_OK):
        raise PermissionError(_('ecriture', dossier=nouveau))
    ancien = reglages()[cle]
    n = 0
    if deplacer:
        if cle == 'dossier_projets':
            n = deplacer_contenu(ancien, nouveau, lambda c: os.path.exists(os.path.join(c, 'projet.json')))
        else:
            n = deplacer_contenu(ancien, nouveau, lambda c: re.fullmatch(r'ggml-.*\.bin|cohere-asr-mlx', os.path.basename(c)))
    sauver_reglages(**{cle: nouveau})
    return n


# Remplacée par la fenêtre native quand Rature tourne en application (voir mac.py)
CHOISIR_DOSSIER = None


def choisir_dossier():
    if CHOISIR_DOSSIER:
        return CHOISIR_DOSSIER()
    r = subprocess.run(['osascript', '-e', 'tell current application to activate',
                        '-e', f'POSIX path of (choose folder with prompt "{_("choisir_dossier")}")'], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


# ------------------------------------------------------------------- modèles

MODELES = {
    'large-v3-turbo': {'nom': 'Large v3 Turbo', 'taille': 1624555275,
                       'descr': {'fr': 'Très bonne précision et rapide : 3 h d\'audio en une dizaine de minutes sur M2.',
                                 'en': 'Very accurate and fast: 3 h of audio in about ten minutes on an M2.'}},
    'large-v3-turbo-q5_0': {'nom': 'Large v3 Turbo compact', 'taille': 574041195,
                            'descr': {'fr': 'Presque aussi précis, trois fois plus léger sur le disque.',
                                      'en': 'Almost as accurate, three times smaller on disk.'}},
    'large-v3': {'nom': 'Large v3', 'taille': 3095033483,
                 'descr': {'fr': 'Le plus précis, environ deux fois plus lent.', 'en': 'The most accurate, about twice as slow.'}},
    'small': {'nom': 'Small', 'taille': 487601967,
              'descr': {'fr': 'Très rapide mais nettement moins précis : pour des essais.',
                        'en': 'Very fast but noticeably less accurate: for quick tests.'}},
    'cohere-transcribe': {
        'nom': 'Cohere Transcribe', 'taille': 2421568781, 'moteur': 'cohere',
        'descr': {'fr': 'Texte très fidèle à l\'oral (hésitations, reprises). Whisper sert en plus à placer les mots dans le temps : '
                        'environ deux fois plus long. 14 langues dont le français et l\'anglais.',
                  'en': 'Text very faithful to speech (hesitations, restarts). Whisper is also used to place the words in time: '
                        'about twice as long. 14 languages including English and French.'},
        'depot': 'https://huggingface.co/appautomaton/cohere-asr-mlx/resolve/main/mlx-int8/',
        'dossier': os.path.join('cohere-asr-mlx', 'mlx-int8'),
        'fichiers': {'model.safetensors': 2419202482, 'tokenizer.json': 1816694, 'tokenizer.model': 492827,
                     'tokenizer_config.json': 48138, 'special_tokens_map.json': 4091, 'config.json': 3764,
                     'generation_config.json': 234, 'preprocessor_config.json': 420, 'processor_config.json': 131}},
}
WHISPER_POUR_TEMPS = ('large-v3-turbo', 'large-v3-turbo-q5_0', 'large-v3', 'small')


# --------------------------------------------------------------- module Cohere
# Les paquets Python de Cohere (MLX…) ne sont pas dans l'app : ils sont téléchargés depuis PyPI avec le modèle,
# vérifiés par leur empreinte SHA-256 et installés dans ~/Library/Application Support/Rature/modules/.

def manifeste_cohere():
    for d in (RESSOURCES, os.path.dirname(os.path.abspath(__file__))):
        f = os.path.join(d, 'module_cohere.json')
        if os.path.exists(f):
            with open(f) as fh:
                return json.load(fh)
    return []


def dossier_module_cohere():
    empreinte = hashlib.sha256(''.join(x['sha256'] for x in manifeste_cohere()).encode()).hexdigest()[:10]
    return os.path.join(SUPPORT, 'modules', f'cohere-{empreinte}')


def module_cohere_installe():
    return bool(manifeste_cohere()) and os.path.exists(os.path.join(dossier_module_cohere(), 'installe.json'))


def installer_module_cohere(ctx, t):
    import zipfile
    import io
    dest = dossier_module_cohere()
    tmp = dest + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for paquet in manifeste_cohere():
        tampon = io.BytesIO()
        with urllib.request.urlopen(paquet['url'], timeout=60, context=ctx) as r:
            while bloc := r.read(1 << 20):
                if t['etat'] == 'annule':
                    raise InterruptedError('annulé')
                tampon.write(bloc)
                t['recu'] += len(bloc)
        if hashlib.sha256(tampon.getvalue()).hexdigest() != paquet['sha256']:
            raise IOError(f'empreinte invalide : {paquet["fichier"]}')
        with zipfile.ZipFile(tampon) as z:                 # une roue (wheel) est une archive zip
            for info in z.infolist():
                nom = re.sub(r'^[^/]+\.data/(purelib|platlib)/', '', info.filename)
                if re.match(r'^[^/]+\.data/', nom) or info.is_dir():
                    continue
                cible = os.path.join(tmp, nom)
                os.makedirs(os.path.dirname(cible), exist_ok=True)
                with z.open(info) as src, open(cible, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
                droits = info.external_attr >> 16
                if droits:
                    os.chmod(cible, droits & 0o777)
    with open(os.path.join(tmp, 'installe.json'), 'w') as f:
        json.dump(manifeste_cohere(), f)
    for ancien in os.listdir(os.path.join(SUPPORT, 'modules')):          # versions précédentes
        if ancien.startswith('cohere-') and os.path.join(SUPPORT, 'modules', ancien) not in (tmp, dest):
            shutil.rmtree(os.path.join(SUPPORT, 'modules', ancien), ignore_errors=True)
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)


MODELES['cohere-transcribe']['taille'] += sum(x['taille'] for x in manifeste_cohere())
URL_MODELES = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{}.bin'
telechargements = {}      # clé -> {'etat', 'recu', 'total', 'message'}


def emplacements_modele(cle):
    if MODELES[cle].get('moteur') == 'cohere':
        return [os.path.join(reglages()['dossier_modeles'], MODELES[cle]['dossier'])]
    nom = f'ggml-{cle}.bin'
    return [os.path.join(reglages()['dossier_modeles'], nom),
            os.path.join(SUPPORT, nom),                                  # emplacement de la version 1.0
            os.path.expanduser(f'~/.cache/whisper-cpp/{nom}')]           # installation whisper.cpp existante


def chemin_modele(cle=None):
    cle = cle or reglages()['modele']
    if MODELES[cle].get('moteur') == 'cohere':          # modèle en plusieurs fichiers, dans un dossier
        d = emplacements_modele(cle)[0]
        ok = all(os.path.exists(os.path.join(d, f)) and os.path.getsize(os.path.join(d, f)) == n
                 for f, n in MODELES[cle]['fichiers'].items())
        return d if ok and module_cohere_installe() else None
    for c in emplacements_modele(cle):
        if os.path.exists(c) and os.path.getsize(c) == MODELES[cle]['taille']:
            return c
    return None


def telecharger_modele(cle=None):
    cle = cle or reglages()['modele']
    if cle not in MODELES:
        raise ValueError(_('modele_inconnu'))
    t = telechargements.get(cle)
    if t and t['etat'] == 'en_cours':
        return
    t = telechargements[cle] = {'etat': 'en_cours', 'recu': 0, 'total': MODELES[cle]['taille'], 'message': ''}

    def travail():
        dest = emplacements_modele(cle)[0]
        part = dest + '.part'
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            libre = espace_libre(dest)
            if libre is not None and libre < MODELES[cle]['taille'] + 200e6:
                raise IOError(_('espace', go=f'{libre / 1e9:.1f}'))
            try:                         # certificats embarqués : indispensable sur un Mac sans Homebrew
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                ctx = None
            if MODELES[cle].get('moteur') == 'cohere':
                if module_cohere_installe():
                    t['recu'] += sum(x['taille'] for x in manifeste_cohere())
                else:
                    installer_module_cohere(ctx, t)
                os.makedirs(dest, exist_ok=True)
                for nom, taille in MODELES[cle]['fichiers'].items():
                    cible = os.path.join(dest, nom)
                    if os.path.exists(cible) and os.path.getsize(cible) == taille:
                        t['recu'] += taille
                        continue
                    part = cible + '.part'
                    with urllib.request.urlopen(MODELES[cle]['depot'] + nom, timeout=60, context=ctx) as r, open(part, 'wb') as f:
                        while bloc := r.read(1 << 20):
                            if t['etat'] == 'annule':
                                raise InterruptedError('annulé')
                            f.write(bloc)
                            t['recu'] += len(bloc)
                    if os.path.getsize(part) != taille:
                        raise IOError(_('incomplet'))
                    os.replace(part, cible)
                t['etat'] = 'pret'
                return
            with urllib.request.urlopen(URL_MODELES.format(cle), timeout=60, context=ctx) as r, open(part, 'wb') as f:
                while bloc := r.read(1 << 20):
                    if t['etat'] == 'annule':
                        raise InterruptedError('annulé')
                    f.write(bloc)
                    t['recu'] += len(bloc)
            if os.path.getsize(part) != MODELES[cle]['taille']:
                raise IOError(_('incomplet'))
            os.replace(part, dest)
            t['etat'] = 'pret'
        except Exception as e:  # noqa: BLE001
            if t['etat'] != 'annule':
                t.update(etat='erreur', message=_('dl_impossible', e=e))
            if os.path.exists(part):
                os.remove(part)

    threading.Thread(target=travail, daemon=True).start()


def etat_modele(cle=None):
    cle = cle or reglages()['modele']
    t = dict(telechargements.get(cle) or {'etat': 'absent', 'recu': 0, 'total': MODELES[cle]['taille'], 'message': ''})
    if t['etat'] not in ('en_cours', 'erreur'):
        t['etat'] = 'pret' if chemin_modele(cle) else 'absent'
    t.update(cle=cle, nom=MODELES[cle]['nom'], chemin=chemin_modele(cle))
    return t


def supprimer_modele(cle):
    c = chemin_modele(cle)
    if not c:
        return
    if MODELES[cle].get('moteur') == 'cohere':
        shutil.rmtree(os.path.dirname(c))
        shutil.rmtree(dossier_module_cohere(), ignore_errors=True)
        return
    if os.path.dirname(c) != reglages()['dossier_modeles']:
        raise ValueError(_('hors_dossier'))
    os.remove(c)


def importer_modele(cle):
    """Range dans le dossier des modèles un modèle trouvé ailleurs (ancienne installation)."""
    c = chemin_modele(cle)
    dest = emplacements_modele(cle)[0]
    if c and c != dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(c, dest)


# ------------------------------------------------------------ mises à jour
# Source : la dernière release GitHub (tag vX.Y.Z, .dmg joint, notes en Markdown) ; l'ancien format version.json
# ({"version", "date", "url", "notes": {"fr", "en"}}) reste compris.
FICHIER_MAJ = os.path.join(SUPPORT, 'maj.json')


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r'\d+', str(v))[:3]) or (0,)


def depuis_github(release):
    """Release GitHub → {version, date, url (le .dmg), page, notes {en, fr}}.

    Les notes viennent du texte de la release : les puces sous « ### New » (anglais) et « ### Nouveautés » (français)."""
    dmg = next((a['browser_download_url'] for a in release.get('assets') or [] if a.get('name', '').endswith('.dmg')), None)
    notes, section = {'en': [], 'fr': []}, None
    for ligne in (release.get('body') or '').splitlines():
        titre = re.match(r'#+\s*(.*)', ligne)
        if titre:
            t = titre.group(1).lower()
            section = 'fr' if 'nouveaut' in t else 'en' if t.startswith('new') else None
        elif section and ligne.lstrip().startswith(('- ', '* ')):
            texte = re.sub(r'[*_`]|\[([^\]]*)\]\([^)]*\)', lambda m: m.group(1) or '', ligne.lstrip()[2:]).strip()
            notes[section].append(re.split(r' — | : ', texte)[0].strip(' .'))
    return {'version': (release.get('tag_name') or '').lstrip('vV'), 'date': (release.get('published_at') or '')[:10],
            'url': dmg or release.get('html_url'), 'page': release.get('html_url'),
            'notes': {k: ' · '.join(v[:5]) for k, v in notes.items() if v}}


def verifier_maj(forcer=False):
    """Compare la version installée à celle publiée sur le site ; résultat gardé 24 h."""
    try:
        with open(FICHIER_MAJ) as f:
            cache = json.load(f)
        if not forcer and time.time() - cache.get('verifie', 0) < 86400:
            return resultat_maj(cache)
    except (OSError, ValueError):
        pass
    cache = {'verifie': time.time()}
    try:
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = None
        req = urllib.request.Request(URL_MAJ, headers={'User-Agent': f'Rature/{VERSION} (macOS)',
                                                       'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            publie = json.loads(r.read().decode())
        cache['publie'] = depuis_github(publie) if 'tag_name' in publie else publie
    except Exception as e:  # noqa: BLE001
        cache['erreur'] = str(e)
    try:
        os.makedirs(SUPPORT, exist_ok=True)
        with open(FICHIER_MAJ, 'w') as f:
            json.dump(cache, f, ensure_ascii=False)
    except OSError:
        pass
    return resultat_maj(cache)


def resultat_maj(cache):
    publie = cache.get('publie') or {}
    derniere = publie.get('version')
    return {'actuelle': VERSION, 'derniere': derniere, 'disponible': bool(derniere) and version_tuple(derniere) > version_tuple(VERSION),
            'url': publie.get('url') or publie.get('page'), 'notes': publie.get('notes') or {}, 'date': publie.get('date'),
            'verifie': cache.get('verifie'), 'erreur': cache.get('erreur')}


def etat_reglages():
    r = reglages()
    return {'reglages': r, 'defauts': DEFAUTS, 'version': VERSION,
            'libre_projets': espace_libre(r['dossier_projets']), 'libre_modeles': espace_libre(r['dossier_modeles']),
            'modeles': [{**info, 'cle': cle, **etat_modele(cle),
                         'hors_dossier': bool(chemin_modele(cle)) and info.get('moteur') != 'cohere'
                         and os.path.dirname(chemin_modele(cle)) != r['dossier_modeles']}
                        for cle, info in MODELES.items()]}


def binaire_whisper():
    return outil('whisper-cli')


# --------------------------------------------------------------- traitement

# Amorce « mot pour mot » : un début de texte plein d'hésitations incite Whisper à transcrire les euh, hum
# et répétitions au lieu de les gommer (ils deviennent alors des mots qu'on peut couper).
INVITE_VERBATIM = {
    'fr': "Euh, alors, hum, bon... euh, je, je voulais dire, hmm, voilà. Ben, euh, c'est, c'est ça, hein.",
    'en': "Um, so, uh, well... um, I, I wanted to say, hmm, you know. Uh, it's, it's like, yeah.",
}

def etape(pid, nom, progression=0, message=''):
    maj(pid, statut='traitement', etape=nom, progression=progression, message=message)


PROCESSUS = set()   # processus externes en cours, arrêtés si l'application se ferme


def arreter():
    for proc in list(PROCESSUS):
        proc.kill()


def afconvert(*args):
    proc = subprocess.Popen(['afconvert', *args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    PROCESSUS.add(proc)
    try:
        sortie = proc.communicate()[0]
    finally:
        PROCESSUS.discard(proc)
    if proc.returncode != 0:
        raise RuntimeError(_('conversion_impossible', e=sortie.strip()[-300:]))


def traiter(pid):
    p = charger(pid)
    d = dossier(pid)
    srcs = p['sources']
    if not srcs:
        raise ValueError(_('aucun_fichier'))
    cle_modele = reglages()['modele']
    while etat_modele(cle_modele)['etat'] == 'en_cours':
        maj(pid, statut='attente', message=_('attente_modele', nom=MODELES[cle_modele]['nom']))
        time.sleep(2)
    modele_ok = chemin_modele(cle_modele)
    if not modele_ok:
        raise FileNotFoundError(_('modele_absent', nom=MODELES[cle_modele]['nom']))
    maj(pid, modele=cle_modele)
    cohere = MODELES[cle_modele].get('moteur') == 'cohere'
    if cohere:                      # Whisper place les mots dans le temps, Cohere écrit le texte
        modele_cohere = modele_ok
        modele_ok = next((chemin_modele(c) for c in WHISPER_POUR_TEMPS if chemin_modele(c)), None)
        if not modele_ok:
            raise FileNotFoundError(_('cohere_sans_whisper'))
    whisper = binaire_whisper()
    if not whisper:
        raise FileNotFoundError(_('whisper_absent'))

    if p.get('genre') == 'sous-titres':
        # sous-titres de film : seul le son en 16 kHz mono sert (ni copie haute qualité, ni aperçu d'écoute)
        wav16, lectures, duree = son_film(pid, d, srcs)
    else:
        # 1. Fichiers de lecture : WAV PCM au format commun (les WAV compatibles sont lus sur place, sans copie)
        etape(pid, 'preparation', 0)
        sr_ref = next((s['sr'] for s in srcs if s['codec'] in ('pcm_s16le', 'pcm_s24le')), 48000)
        ch_ref = max(s['ch'] for s in srcs)
        lectures = []
        for k, s in enumerate(srcs):
            s['chemin'] = resoudre(pid, s['chemin'])
            if not os.path.exists(s['chemin']):
                raise FileNotFoundError(_('fichier_introuvable', chemin=s['chemin']))
            ok = s['codec'] in ('pcm_s16le', 'pcm_s24le') and s['sr'] == sr_ref and s['ch'] == ch_ref
            if ok:
                try:
                    with wave.open(s['chemin'], 'rb') as w:
                        n = w.getnframes()
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                lecture = s['chemin']
            else:
                os.makedirs(os.path.join(d, 'convertis'), exist_ok=True)
                lecture = os.path.join(d, 'convertis', f'{k + 1:02d}_{os.path.splitext(s["nom"])[0]}.wav')
                etape(pid, 'preparation', round(k / len(srcs) * 50), _('conversion', nom=s['nom']))
                if s.get('via') == 'ffmpeg':
                    extraire_ffmpeg(s['chemin'], lecture, s.get('piste', 0), ch_ref, sr_ref)
                else:
                    afconvert('-f', 'WAVE', '-d', f'LEI24@{sr_ref}', '-c', str(ch_ref), *(['--mix'] if s['ch'] > ch_ref else []),
                              s['chemin'], lecture)
                with wave.open(lecture, 'rb') as w:
                    n = w.getnframes()
            lectures.append({'fichier': s['nom'], 'lecture': lecture, 'n': n, 'sr': sr_ref, 'heure': s.get('heure')})
        t = 0.0
        for L in lectures:
            L['debut'] = t
            L['duree'] = L['n'] / L['sr']
            t += L['duree']
        duree = t
        json.dump(lectures, open(os.path.join(d, 'sources.json'), 'w'), ensure_ascii=False, indent=1)

        # 2. Timeline assemblée à 32 kHz (temporaire) → aperçu AAC léger + version 16 kHz mono pour l'analyse
        tmp, morceau = os.path.join(d, 'assemblage.tmp.wav'), os.path.join(d, 'morceau.tmp.wav')
        wav16 = os.path.join(d, 'analyse_16k.wav')
        with wave.open(tmp, 'wb') as out:
            out.setnchannels(ch_ref)
            out.setsampwidth(2)
            out.setframerate(32000)
            for k, L in enumerate(lectures):
                etape(pid, 'preparation', 50 + round(k / len(lectures) * 35), _('assemblage', nom=L['fichier']))
                afconvert('-f', 'WAVE', '-d', 'LEI16@32000', L['lecture'], morceau)
                with wave.open(morceau, 'rb') as w:
                    attendu = round(L['duree'] * 32000)
                    brut = w.readframes(attendu)
                # aligner exactement chaque morceau sur la durée de sa source (évite toute dérive)
                manque = attendu * 2 * ch_ref - len(brut)
                out.writeframes(brut + b'\0' * manque if manque > 0 else brut)
        os.remove(morceau)
        etape(pid, 'preparation', 88, _('apercu'))
        afconvert('-f', 'm4af', '-d', 'aac', '-b', '96000' if ch_ref == 1 else '160000', tmp, os.path.join(d, 'apercu.m4a'))
        afconvert('-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', *(['--mix'] if ch_ref > 1 else []), tmp, wav16)
        os.remove(tmp)

    # 3. Transcription
    etape(pid, 'transcription', 0)
    # amorce « mot pour mot » : jamais avec Cohere (Whisper ne sert alors qu'à placer les mots, et l'amorce
    # peut lui faire sauter une phrase)
    invite = ' '.join(x for x in (INVITE_VERBATIM.get(p.get('langue')) if p.get('verbatim') and not cohere else '',
                                   p.get('vocabulaire', '').strip()) if x)
    transcrire_whisper(pid, d, wav16, whisper, modele_ok, p.get('langue') or 'fr', invite, 0.5 if cohere else 1.0,
                       [L['debut'] for L in lectures[1:]])

    if cohere:
        transcrire_cohere(pid, d, wav16, modele_cohere, p.get('langue') or 'auto')

    # 4. Mots, points de coupe, forme d'onde
    etape(pid, 'analyse', 50)
    preparer(pid, wav16)
    os.remove(wav16)
    if p.get('genre') == 'sous-titres':                  # sous-titres de film : le SRT tout de suite
        etape(pid, 'sous_titres', 99)
        srt_film(pid)
    maj(pid, statut='pret', etape=None, progression=100, message='', duree=duree)


DUREE_BLOC_WHISPER = 600          # Whisper travaille par blocs de 10 min : une dérive ne peut pas s'y propager


def empreinte_fichier(f):
    h = hashlib.sha1()
    with open(f, 'rb') as e:
        for morceau in iter(lambda: e.read(1 << 20), b''):
            h.update(morceau)
    return h.hexdigest()


def decouper_wav16(wav16, dossier, duree=DUREE_BLOC_WHISPER, frontieres=()):
    """Découpe le WAV 16 kHz en blocs coupés dans un passage calme ; renvoie [(fichier, début s, durée s, part de voix)].

    Un bloc ne chevauche jamais deux fichiers sources (`frontieres`, en s) : à la jonction de deux clips, Whisper
    plaçait les premiers mots du second quelques secondes trop tôt."""
    os.makedirs(dossier, exist_ok=True)
    blocs = []
    with wave.open(wav16) as w:
        sr, n = w.getframerate(), w.getnframes()
        bornes = sorted(int(round(f * sr)) for f in frontieres if 0 < f * sr < n)
        debut, k = 0, 0
        while debut < n:
            fin = min(n, debut + duree * sr)
            prochaine = next((b for b in bornes if b > debut), None)
            if prochaine is not None and prochaine <= fin:
                fin = prochaine                           # fin du fichier source : le bloc s'arrête là
            elif fin < n:                                 # chercher le creux dans les 20 dernières secondes
                w.setpos(fin - 20 * sr)
                x = np.frombuffer(w.readframes(20 * sr), dtype='<i2').astype(np.float32)
                hop = sr // 100
                env = np.sqrt((x[:len(x) // hop * hop].reshape(-1, hop) ** 2).mean(1))
                fin = fin - 20 * sr + int(np.argmin(env)) * hop
            w.setpos(debut)
            x = np.frombuffer(w.readframes(fin - debut), dtype='<i2')
            f = os.path.join(dossier, f'bloc{k:04d}.wav')
            with wave.open(f, 'wb') as o:
                o.setnchannels(1); o.setsampwidth(2); o.setframerate(sr)
                o.writeframes(x.tobytes())
            hop = sr // 100
            rms = np.sqrt((x[:len(x) // hop * hop].astype(np.float32).reshape(-1, hop) ** 2).mean(1)) if len(x) >= hop else np.zeros(1)
            voix = float((rms > 330).mean())             # part des blocs de 10 ms au-dessus de −40 dBFS
            blocs.append((f, debut / sr, (fin - debut) / sr, voix))
            debut, k = fin, k + 1
    return blocs


def bloc_rate(mots, duree, voix):
    """Signes d'un décrochage de Whisper : presque pas de mots alors qu'on parle, ou un texte qui boucle."""
    if voix > 0.25 and len(mots) < 12 * duree / 60:
        return True
    cles = [re.sub(r'\W+', '', m.lower()) for m in mots]
    tri = [' '.join(cles[i:i + 3]) for i in range(len(cles) - 2)]
    if len(tri) >= 30:
        from collections import Counter
        if Counter(tri).most_common(1)[0][1] > 0.15 * len(tri):
            return True
    return False


def transcrire_whisper(pid, d, wav16, whisper, modele, langue, invite, part, frontieres=()):
    """Transcription Whisper par blocs de 10 min (un seul lancement) ; les blocs ratés sont refaits sans contexte."""
    tmp = os.path.join(d, 'blocs.tmp')
    # reprise après une interruption : un bloc déjà transcrit n'est pas refait si son son est identique au bit près
    anciens = {}
    if os.path.isdir(tmp):
        for nom in os.listdir(tmp):
            f = os.path.join(tmp, nom)
            if nom.endswith('.wav') and os.path.exists(f + '.json'):
                try:
                    anciens[nom] = (empreinte_fichier(f), json.load(open(f + '.json')))
                except (OSError, ValueError):
                    pass
    shutil.rmtree(tmp, ignore_errors=True)
    blocs = decouper_wav16(wav16, tmp, frontieres=frontieres)
    deja = set()
    for f, *autres in blocs:
        ancien = anciens.get(os.path.basename(f))
        if ancien and ancien[0] == empreinte_fichier(f):
            with open(f + '.json', 'w') as sortie:
                json.dump(ancien[1], sortie, ensure_ascii=False)
            deja.add(f)
    if deja:
        with open(os.path.join(d, 'transcription.log'), 'a') as log:
            log.write(f'Reprise : {len(deja)} bloc(s) sur {len(blocs)} déjà transcrits\n')
    base = [whisper, '-m', modele, '-l', langue, '-t', str(min(8, os.cpu_count() or 4)), '-ml', '1', '-sow', '-oj', '-pp']
    if invite:
        base += ['--prompt', invite]
    debut = time.time()

    def lancer(fichiers, options, poids0, poids1):
        cmd = base + options + [x for f in fichiers for x in ('-f', f)]
        with open(os.path.join(d, 'transcription.log'), 'a') as log:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, errors='replace')
            PROCESSUS.add(proc)
            try:
                i, dernier = 0, -1
                for ligne in proc.stderr:
                    log.write(ligne)
                    m = re.search(r'progress =\s*(\d+)%', ligne)
                    if not m:
                        continue
                    pct = int(m.group(1))
                    if pct < dernier:
                        i += 1
                    dernier = pct
                    fait = (i * 100 + pct) / (100 * len(fichiers))
                    total = poids0 + (poids1 - poids0) * fait
                    ecoule = time.time() - debut
                    reste = ecoule / total * (1 - total) if total > 0.03 else None
                    etape(pid, 'transcription', round(100 * total * part),
                          _('restant', n=max(1, round(reste / 60))) if reste else '')
            finally:
                PROCESSUS.discard(proc)
                if proc.poll() is None:                   # erreur pendant la lecture : arrêter Whisper
                    proc.kill()
                code = proc.wait()
            if code != 0:
                raise RuntimeError(_('transcription_echec'))

    a_faire = [b[0] for b in blocs if b[0] not in deja]
    if a_faire:
        lancer(a_faire, [], 0.9 * (len(blocs) - len(a_faire)) / len(blocs), 0.9)
    resultats = {}
    for f, t0, duree, voix in blocs:
        segs = json.load(open(f + '.json'))['transcription'] if os.path.exists(f + '.json') else []
        resultats[f] = segs
    rates = [b for b in blocs if bloc_rate([s['text'].strip() for s in resultats[b[0]] if s['text'].strip()], b[2], b[3])]
    if rates:                                             # seconde chance, sans contexte (pas de boucle possible)
        with open(os.path.join(d, 'transcription.log'), 'a') as log:
            log.write(f'Blocs refaits sans contexte : {[round(b[1]) for b in rates]}\n')
        lancer([b[0] for b in rates], ['-mc', '0'], 0.9, 1.0)
        for f, *autres in rates:
            resultats[f] = json.load(open(f + '.json'))['transcription']
    langue_detectee = None
    tout = []
    for f, t0, duree, voix in blocs:
        decalage = int(round(t0 * 1000))
        for s_ in resultats[f]:
            tout.append({'offsets': {'from': s_['offsets']['from'] + decalage, 'to': s_['offsets']['to'] + decalage},
                         'text': s_['text']})
        if not langue_detectee and os.path.exists(f + '.json'):
            langue_detectee = (json.load(open(f + '.json')).get('result') or {}).get('language')
    json.dump({'result': {'language': langue_detectee or langue}, 'transcription': tout},
              open(os.path.join(d, 'transcription_brute.json'), 'w'), ensure_ascii=False)
    shutil.rmtree(tmp, ignore_errors=True)


def commande_cohere():
    """Dans l'app : l'app elle-même avec --cohere ; en développement : un Python qui dispose de MLX."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--cohere']
    ici = os.path.dirname(os.path.abspath(__file__))
    py = os.path.join(ici, '..', 'build', 'venv', 'bin', 'python')          # Python de l'app (numpy compris)
    return [py if os.path.exists(py) else sys.executable, os.path.join(ici, 'cohere_worker.py')]


def transcrire_cohere(pid, d, wav16, modele, langue):
    """Seconde passe : texte de Cohere, aligné sur les mots horodatés de Whisper."""
    import alignement
    brut = os.path.join(d, 'transcription_brute.json')
    whisper = json.load(open(brut))
    shutil.copy(brut, os.path.join(d, 'transcription_whisper.json'))
    if langue == 'auto':
        langue = (whisper.get('result') or {}).get('language') or 'fr'
    sortie = os.path.join(d, 'transcription_cohere.json')
    debut = time.time()
    with open(os.path.join(d, 'transcription.log'), 'a') as log:
        env = dict(os.environ, RATURE_MODULE_COHERE=dossier_module_cohere(), PYTHONNOUSERSITE='1')
        proc = subprocess.Popen(commande_cohere() + [wav16, sortie, langue, modele], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors='replace', env=env)
        PROCESSUS.add(proc)
        try:
            for ligne in proc.stdout:
                log.write(ligne)
                m = re.search(r'progress =\s*(\d+)%', ligne)
                if m:
                    pct = int(m.group(1))
                    reste = (time.time() - debut) / pct * (100 - pct) if pct >= 3 else None
                    etape(pid, 'transcription', 50 + pct // 2, _('restant', n=max(1, round(reste / 60))) if reste else _('passe_cohere'))
        finally:
            PROCESSUS.discard(proc)
            if proc.poll() is None:
                proc.kill()
            code = proc.wait()
        if code != 0:
            raise RuntimeError(_('transcription_echec'))
    mots_whisper = [(s['text'].strip(), s['offsets']['from'] / 1000, s['offsets']['to'] / 1000)
                    for s in whisper['transcription'] if s['text'].strip() and not s['text'].strip().startswith('[_')]
    env = enveloppe(wav16)                                # parole détectée dans le son, par blocs de 10 ms
    db = 20 * np.log10(env + 1e-6)
    p10, p90 = np.percentile(db, 10), np.percentile(db, 90)
    voix = db > p10 + 0.35 * (p90 - p10)
    mots = alignement.aligner_par_morceaux(mots_whisper, json.load(open(sortie)), voix, PAS_ENV)
    json.dump(alignement.vers_whisper_json(mots), open(brut, 'w'), ensure_ascii=False)


def ouvrier():
    while True:
        pid = file_travaux.get()
        try:
            traiter(pid)
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f'{datetime.now():%Y-%m-%d %H:%M:%S} Erreur de traitement {pid}', file=sys.stderr)
            traceback.print_exc()
            for essai in range(10):                       # ne jamais laisser le projet figé « en cours »
                try:
                    maj(pid, statut='erreur', etape=None, message=str(e) or type(e).__name__)
                    break
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    time.sleep(3)


def lancer(pid):
    p = charger(pid)
    if p['statut'] in ('attente', 'traitement'):
        return p
    if not p['sources']:
        raise ValueError(_('ajouter_fichier'))
    p = maj(pid, statut='attente', etape=None, progression=0, message='')
    file_travaux.put(pid)
    return p


def en_cours():
    return any(r['statut'] in ('attente', 'traitement') for r in lister())


# -------------------------------------------------------------- préparation

def enveloppe(wav16):
    with wave.open(wav16, 'rb') as w:
        sr = w.getframerate()
        brut = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32)
    hop = int(sr * PAS_ENV)
    n = len(brut) // hop
    return np.sqrt((brut[:n * hop].reshape(n, hop) ** 2).mean(1))


def ecrire_crete(pid, env):
    """Forme d'onde d'aperçu : N_CRETE valeurs 0–255 (échelle racine)."""
    n = len(env)
    bornes = np.linspace(0, n, N_CRETE + 1).astype(int)
    c = np.array([env[a:b].max() if b > a else 0 for a, b in zip(bornes, bornes[1:])])
    c = np.sqrt(c / (np.percentile(c, 99.5) + 1e-6))
    json.dump([int(v) for v in np.clip(c * 255, 0, 255)], open(chemin(pid, 'crete.json'), 'w'))


def creux(env, a, b, cible):
    """Instant de plus faible énergie dans [a, b], en favorisant la proximité de `cible`."""
    i0, i1 = max(0, int(a / PAS_ENV)), min(len(env), int(b / PAS_ENV) + 1)
    if i1 - i0 < 2:
        return cible
    seg = env[i0:i1]
    t = (np.arange(i0, i1) + 0.5) * PAS_ENV
    score = seg / (seg.max() + 1e-6) + 0.15 * np.abs(t - cible) / max(b - a, 1e-3)
    return float(t[int(np.argmin(score))])


def preparer(pid, wav16):
    d = dossier(pid)
    brut = json.load(open(os.path.join(d, 'transcription_brute.json')))['transcription']
    srcs = json.load(open(os.path.join(d, 'sources.json')))
    duree = sum(s['duree'] for s in srcs)
    mots = []
    for seg in brut:
        txt = seg['text'].strip()
        if not txt or txt.startswith('[_'):
            continue
        t0, t1 = seg['offsets']['from'] / 1000, seg['offsets']['to'] / 1000
        if mots and PONCT_SEULE.match(txt):
            mots[-1]['x'] += (' ' + txt) if txt[0] in '?!:;' else txt
            mots[-1]['t1'] = max(mots[-1]['t1'], t1)
            continue
        mots.append({'x': txt, 't0': t0, 't1': max(t1, t0 + 0.05)})

    env = enveloppe(wav16)
    np.save(os.path.join(d, 'enveloppe.npy'), env.astype(np.float32))
    ecrire_crete(pid, env)

    # Jetons : mots + pauses longues. Chaque jeton possède l'audio [d, d_suivant[.
    jetons = []
    for i, m in enumerate(mots):
        prev = mots[i - 1] if i else None
        if prev and m['t0'] - prev['t1'] > PAUSE_MIN:
            debut_pause = creux(env, prev['t1'], prev['t1'] + 0.35, prev['t1'] + 0.12)
            jetons.append({'p': 1, 't0': debut_pause, 't1': m['t0'], 'd': debut_pause})
        if i == 0:
            dd = 0.0
        else:
            bas = max(m['t0'] - 0.20, jetons[-1]['d'] + 0.05)
            haut = min(m['t0'] + 0.03, m['t1'] - 0.02)
            dd = creux(env, bas, haut, m['t0']) if haut > bas else m['t0']
        jetons.append({'x': m['x'], 't0': m['t0'], 't1': m['t1'], 'd': dd})

    # Paragraphes : nouvelle source, longue pause, ou fin de phrase après ~70 mots
    bornes = [s['debut'] for s in srcs[1:]]
    par, n_mots, ib = 0, 0, 0
    for i, j in enumerate(jetons):
        nouveau = False
        while ib < len(bornes) and j['t0'] >= bornes[ib] - 0.01:
            ib += 1
            nouveau = True
        if i and not j.get('p'):
            prec = jetons[i - 1]
            if prec.get('p') and prec['t1'] - prec['t0'] > 4:
                nouveau = True
            elif n_mots > 70 and re.search(r'[\.\?\!…]$', prec.get('x', '')):
                nouveau = True
        if nouveau and i:
            par += 1
            n_mots = 0
        j['g'] = par
        j['i'] = i
        if not j.get('p'):
            n_mots += 1
        for k in ('t0', 't1', 'd'):
            j[k] = round(j[k], 3)

    public = [{k: s.get(k) for k in ('fichier', 'debut', 'duree', 'heure')} for s in srcs]
    json.dump({'duree': duree, 'sources': public, 'jetons': jetons},
              open(os.path.join(d, 'mots.json'), 'w'), ensure_ascii=False, separators=(',', ':'))


# -------------------------------------------------------------------- export

class Lecteur:
    """Lit un intervalle de la timeline assemblée directement dans les fichiers sources."""

    def __init__(self, srcs):
        self.srcs = srcs
        self.w = {}
        self.sr = srcs[0]['sr']
        self.depart = np.cumsum([0] + [s['n'] for s in srcs])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        for w in self.w.values():
            w.close()

    def fichier(self, k):
        if k not in self.w:
            self.w[k] = wave.open(self.srcs[k]['lecture'], 'rb')
        return self.w[k]

    def lire(self, a, b):
        i0, i1 = int(round(a * self.sr)), int(round(b * self.sr))
        morceaux = []
        k = int(np.searchsorted(self.depart, i0, side='right') - 1)
        while i0 < i1 and k < len(self.srcs):
            fin_k = self.depart[k + 1]
            j1 = min(i1, fin_k)
            w = self.fichier(k)
            w.setpos(int(i0 - self.depart[k]))
            morceaux.append(decoder(w.readframes(int(j1 - i0)), w.getsampwidth(), w.getnchannels()))
            i0 = j1
            k += 1
        return np.concatenate(morceaux) if morceaux else np.zeros((0, 1))


def decoder(octets, sw, nc):
    o = np.frombuffer(octets, dtype=np.uint8)
    if sw == 3:
        o = o.reshape(-1, 3).astype(np.int32)
        v = o[:, 0] | (o[:, 1] << 8) | (o[:, 2] << 16)
        v = np.where(v & 0x800000, v - 0x1000000, v)
        return v.reshape(-1, nc).astype(np.float64)
    if sw == 2:
        return np.frombuffer(octets, dtype='<i2').reshape(-1, nc).astype(np.float64) * 256
    raise ValueError(f'Format non géré : {sw * 8} bits')


def encoder_24(v):
    v = np.clip(np.round(v), -0x800000, 0x7FFFFF).astype(np.int32).reshape(-1)
    o = np.empty((len(v), 3), dtype=np.uint8)
    o[:, 0] = v & 0xFF
    o[:, 1] = (v >> 8) & 0xFF
    o[:, 2] = (v >> 16) & 0xFF
    return o.tobytes()


def intervalles_gardes(jetons, supprimes, duree):
    garde = []
    for k, j in enumerate(jetons):
        if j['i'] in supprimes:
            continue
        a = j['d']
        b = jetons[k + 1]['d'] if k + 1 < len(jetons) else duree
        if garde and abs(garde[-1][1] - a) < 1e-6:
            garde[-1][1] = b
        else:
            garde.append([a, b])
    return garde


ABREVIATIONS = {'mr.', 'mrs.', 'ms.', 'dr.', 'st.', 'jr.', 'sr.', 'vs.', 'etc.', 'm.', 'mme.', 'mlle.', 'p.', 'cf.', 'n°.'}


def fmt_srt(t):
    ms = int(round(t * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def fmt_hms(t):
    t = int(t)
    return f'{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}'



def outil(nom):
    """Binaire embarqué (whisper-cli, lame, rnnoise) : dans l'app, sinon dans le dossier de build."""
    build = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'build')
    for c in (os.path.join(RESSOURCES, 'bin', nom),
              os.path.join(build, 'lame-3.100', 'frontend', 'lame') if nom == 'lame' else '',
              os.path.join(build, 'rnnoise', 'rnnoise') if nom == 'rnnoise' else '',
              os.path.join(build, 'ffmpeg-9.0.2', 'ffmpeg') if nom == 'ffmpeg' else '',
              # FFmpeg installé sur le Mac (Homebrew…) : une app n'hérite pas du PATH du Terminal
              *(('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg') if nom == 'ffmpeg' else ())):
        if c and os.path.exists(c):
            return c
    return shutil.which(nom)


DOSSIER_TELECHARGEMENTS = os.path.expanduser('~/Downloads')
EXPORT_DEFAUT = {'format': 'mp3', 'debit': 96, 'mono': True, 'ameliorer': True, 'bruit': 'leger', 'egaliseur': True,
                 'nivellement': True, 'volume': 'podcast', 'limiteur': True, 'silences': True,
                 'fichiers': {'texte': True, 'srt': True, 'srt_video': True, 'coupes': False, 'forts': True},
                 'zip': True, 'dossier': DOSSIER_TELECHARGEMENTS}
EXPORTS_PRODUITS = set()          # fichiers créés par un export (seuls révélables dans le Finder)
TACHES = {}


def options_export(o=None):
    r = json.loads(json.dumps(EXPORT_DEFAUT))
    for source in (reglages().get('export') or {}, o or {}):
        for k, v in source.items():
            if k == 'fichiers' and isinstance(v, dict):    # fusion : un fichier joint ajouté depuis garde sa valeur par défaut
                r['fichiers'].update(v)
            elif k in r:
                r[k] = v
    if r['format'] not in ('mp3', 'm4a', 'wav'):
        r['format'] = 'mp3'
    r['debit'] = int(r['debit']) if int(r['debit']) in (48, 64, 96, 128, 160, 192, 256) else 96
    if not os.path.isdir(os.path.expanduser(r['dossier'] or '')):
        r['dossier'] = DOSSIER_TELECHARGEMENTS
    return r


def donnees_montage(pid, etat):
    d = dossier(pid)
    donnees = json.load(open(os.path.join(d, 'mots.json')))
    srcs = json.load(open(os.path.join(d, 'sources.json')))
    for s_ in srcs:
        s_['lecture'] = resoudre(pid, s_['lecture'])
    supprimes = set(etat.get('supprimes', []))
    garde = intervalles_gardes(donnees['jetons'], supprimes, donnees['duree'])
    if not garde:
        raise ValueError(_('tout_coupe'))
    return donnees['jetons'], srcs, supprimes, garde


def raccourcir_silences(pid, garde, seuil=0.8, cible=0.5):
    """Raccourcit les pauses de plus de `seuil` s à `cible` s, d'après l'énergie du son (blocs de 10 ms)."""
    f = chemin(pid, 'enveloppe.npy')
    if not os.path.exists(f):
        return garde
    env = np.load(f)
    db = 20 * np.log10(env + 1e-6)
    p10, p90 = np.percentile(db, 10), np.percentile(db, 90)
    calme = db < p10 + 0.3 * (p90 - p10)
    nouveau = []
    for a, b in garde:
        i0, i1 = int(np.ceil(a / PAS_ENV)), int(b / PAS_ENV)
        if i1 - i0 < 2:
            nouveau.append([a, b])
            continue
        c = np.concatenate([[False], calme[i0:i1], [False]]).astype(np.int8)
        bords = np.flatnonzero(np.diff(c))
        debut = a
        for r0, r1 in zip(bords[::2], bords[1::2]):
            t0, t1 = (i0 + r0) * PAS_ENV, (i0 + r1) * PAS_ENV
            if t1 - t0 >= seuil:
                nouveau.append([debut, t0 + cible / 2])
                debut = t1 - cible / 2
        nouveau.append([debut, b])
    return [[x, y] for x, y in nouveau if y - x > 0.02]


def rendre_montage(srcs, garde, dest, mono, progression=lambda p: None):
    """Assemble les passages conservés (fondus aux raccords) dans un WAV 24 bits, en mono si demandé."""
    total = sum(b - a for a, b in garde) or 1
    fait = 0.0
    with Lecteur(srcs) as lec, wave.open(dest, 'wb') as dst:
        sr, nc = lec.sr, lec.fichier(0).getnchannels()
        dst.setnchannels(1 if mono else nc)
        dst.setsampwidth(3)
        dst.setframerate(sr)
        nf = int(FONDU * sr)
        rampe = (0.5 - 0.5 * np.cos(np.linspace(0, np.pi, nf)))[:, None]
        for k, (a, b) in enumerate(garde):
            v = lec.lire(a, b)
            if mono and v.shape[1] > 1:
                v = v.mean(axis=1, keepdims=True)
            if len(v) > 2 * nf:
                if k > 0:
                    v[:nf] *= rampe
                if k < len(garde) - 1:
                    v[-nf:] *= rampe[::-1]
            dst.writeframes(encoder_24(v))
            fait += b - a
            progression(fait / total)
    return sr


def ecrire_annexes(base, jetons, srcs, supprimes, garde, etat, quoi):
    """Texte, sous-titres, liste des coupes et moments forts ; renvoie la liste des fichiers écrits."""
    corr = {int(k): v for k, v in etat.get('corrections', {}).items()}
    fichiers = []

    def vers_montage(t):
        cumul = 0.0
        for a, b in garde:
            if t < a:
                return cumul
            if t < b:
                return cumul + t - a
            cumul += b - a
        return cumul

    def source_de(t):
        for s in reversed(srcs):
            if t >= s['debut'] - 1e-6:
                return s
        return srcs[0]

    if quoi.get('coupes'):
        fichiers.append(base + '_' + _('fichier_coupes') + '.csv')
        with open(fichiers[-1], 'w') as f:
            f.write(_('csv') + '\n')
            cumul, n = 0.0, 0
            for a, b in garde:
                while a < b - 1e-6:           # un segment à cheval sur deux fichiers est scindé
                    s = source_de(a)
                    b2 = min(b, s['debut'] + s['duree'])
                    n += 1
                    f.write(f'{n};{cumul:.3f};{cumul + b2 - a:.3f};{a:.3f};{b2:.3f};{s["fichier"]};'
                            f'{a - s["debut"]:.3f};{b2 - s["debut"]:.3f}\n')
                    cumul += b2 - a
                    a = b2

    gardes = [j for j in jetons if j['i'] not in supprimes and not j.get('p')]
    if quoi.get('texte'):
        paragraphes = {}
        for j in gardes:
            paragraphes.setdefault(j['g'], []).append(j)
        fichiers.append(base + '.txt')
        with open(fichiers[-1], 'w') as f:
            for js in paragraphes.values():
                f.write(f'[{fmt_hms(vers_montage(js[0]["d"]))}] ' + ' '.join(corr.get(j['i'], j['x']) for j in js) + '\n\n')
    forts = sorted(etat.get('forts') or [], key=lambda m: m['de'])
    if quoi.get('forts') and forts:      # moments forts, avec leur position dans le montage
        fichiers.append(base + '_' + _('fichier_forts') + '.txt')
        with open(fichiers[-1], 'w') as f:
            for m in forts:
                mots = [corr.get(j['i'], j['x']) for j in jetons[m['de']:m['a'] + 1] if not j.get('p')]
                coupe = all(j['i'] in supprimes for j in jetons[m['de']:m['a'] + 1])
                note = (m.get('note') or '').strip()
                f.write(f"[{fmt_hms(vers_montage(jetons[m['de']]['d']))}]{' (' + _('coupe') + ')' if coupe else ''}"
                        f"{' ' + note + ' —' if note else ''} {' '.join(mots)}\n\n")
    def deux_lignes(texte, largeur=42):
        """Au-delà de `largeur` caractères : deux lignes équilibrées, coupées de préférence après une ponctuation."""
        if len(texte) <= largeur:
            return texte
        espaces = [k for k, c in enumerate(texte) if c == ' ']
        if not espaces:
            return texte
        milieu = len(texte) / 2
        k = min(espaces, key=lambda e: abs(e - milieu) - (6 if texte[e - 1] in ',;:.?!…' else 0))
        return texte[:k] + '\n' + texte[k + 1:]

    def ecrire_srt(chemin_srt, vers_temps, mots):
        """Sous-titres lisibles : au plus deux lignes d'environ 42 caractères et 7 s à l'écran ; un nouveau
        sous-titre à chaque fin de phrase, à chaque paragraphe et après un silence."""
        fichiers.append(chemin_srt)
        with open(chemin_srt, 'w', encoding='utf-8') as f:
            n, bloc, fin_prec = 0, [], 0
            for k, j in enumerate(mots):
                bloc.append(j)
                texte = ' '.join(corr.get(b['i'], b['x']) for b in bloc)
                mot = corr.get(j['i'], j['x'])
                fin_phrase = re.search(r'[\.\?\!…]$', mot) and mot.lower() not in ABREVIATIONS
                suivant = mots[k + 1] if k + 1 < len(mots) else None
                rupture = suivant is None or suivant['g'] != j['g']
                silence = suivant is not None and suivant['d'] - j['t1'] > 1.0
                prochain = len(corr.get(suivant['i'], suivant['x'])) + 1 if suivant else 0
                plein = len(texte) + prochain > 84 or (suivant is not None and suivant['t1'] - bloc[0]['d'] > 7.0)
                if rupture or silence or plein or (fin_phrase and len(texte) >= 18):
                    n += 1
                    # en millisecondes : jamais de chevauchement avec le sous-titre précédent (refusé par
                    # YouTube et les outils vidéo), au moins 0,3 s d'affichage
                    t0 = max(round(vers_temps(bloc[0]['d']) * 1000), fin_prec)
                    t1 = round(vers_temps(min(bloc[-1]['t1'] + 0.2, suivant['d'] if suivant else bloc[-1]['t1'] + 0.2)) * 1000)
                    # pas plus longtemps que le temps de lecture (sinon le texte reste affiché pendant la musique)
                    t1 = min(t1, t0 + round(1000 * max(1.5, 1.0 + 0.08 * len(texte))))
                    t1 = max(t1, t0 + 300)
                    fin_prec = t1
                    f.write(f'{n}\n{fmt_srt(t0 / 1000)} --> {fmt_srt(t1 / 1000)}\n{deux_lignes(texte)}\n\n')
                    bloc = []

    if quoi.get('srt'):                  # calés sur l'audio exporté (coupes et silences raccourcis compris)
        ecrire_srt(base + '.srt', vers_montage, gardes)
    if quoi.get('srt_video'):            # calés sur l'enregistrement d'origine (vidéo non montée)
        if len(srcs) == 1:
            ecrire_srt(base + '_' + _('fichier_video') + '.srt', lambda t: t, gardes)
        else:                            # plusieurs fichiers (clips vidéo…) : un SRT par fichier, à son propre temps
            for s in srcs:
                a, b = s['debut'], s['debut'] + s['duree']
                mots = [j for j in gardes if a <= (j['t0'] + j['t1']) / 2 < b]     # le milieu du mot décide du clip
                if mots:
                    ecrire_srt(base + '_' + os.path.splitext(s['fichier'])[0] + '.srt',
                               lambda t, a=a, b=b: min(max(t, a), b) - a, mots)
    return fichiers


def cible_lufs(o):
    if not o['ameliorer'] or o['volume'] == 'aucun':
        return None
    if o['volume'] == 'radio':
        return -23.0
    return -19.0 if o['mono'] else -16.0      # podcast / web : −16 LUFS en stéréo, −19 en mono


def traiter_et_encoder(montage, sortie_sans_ext, o, titre, tmp, progression):
    """Améliore la voix si demandé puis encode ; renvoie le chemin du fichier audio final."""
    import traitement
    bits = 24 if o['format'] == 'wav' else 16
    source = montage
    if o['ameliorer']:
        etapes = {'bruit': (0, 40), 'traitement': (40, 75), 'ecriture': (75, 100)}
        source = os.path.join(tmp, 'traite.wav')
        traitement.ameliorer(montage, source, {
            'bruit': o['bruit'], 'egaliseur': o['egaliseur'], 'nivellement': o['nivellement'],
            'cible_lufs': cible_lufs(o), 'limiteur': o['limiteur'], 'bits': bits},
            {'rnnoise': outil('rnnoise')}, os.path.join(tmp, 'traitement'),
            lambda e, p: progression('voix', etapes[e][0] + (etapes[e][1] - etapes[e][0]) * p / 100))
    progression('encodage', 0)
    if o['format'] == 'wav':
        final = sortie_sans_ext + '.wav'
        shutil.move(source, final)
    elif o['format'] == 'mp3':
        lame = outil('lame')
        if not lame:
            raise FileNotFoundError('lame')
        if bits == 16 and source == montage:                 # LAME : entrée 16 bits
            s16 = os.path.join(tmp, 'montage16.wav')
            afconvert('-f', 'WAVE', '-d', 'LEI16', source, s16)
            source = s16
        final = sortie_sans_ext + '.mp3'
        with wave.open(source) as w:
            canaux = w.getnchannels()
        r = subprocess.run([lame, '--silent', '--noreplaygain', '-b', str(o['debit']), '--cbr',
                            '-m', 'm' if canaux == 1 else 'j', '--tt', titre, '--tc', 'Rature', source, final],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('MP3 : ' + (r.stderr or r.stdout).strip()[-300:])
    else:
        final = sortie_sans_ext + '.m4a'
        afconvert('-f', 'm4af', '-d', 'aac', '-b', str(o['debit'] * 1000), '-q', '127', source, final)
    progression('encodage', 100)
    return final


def nom_libre(chemin):
    base, ext = os.path.splitext(chemin)
    i = 2
    while os.path.exists(chemin):
        chemin = f'{base} ({i}){ext}'
        i += 1
    return chemin


def langue_transcrite(pid):
    """Langue de la transcription (détectée par Whisper si « auto »)."""
    for nom in ('transcription_whisper.json', 'transcription_brute.json'):
        f = chemin(pid, nom)
        if os.path.exists(f):
            langue = (json.load(open(f)).get('result') or {}).get('language')
            if langue and langue != 'auto':
                return langue
    langue = charger(pid).get('langue')
    return langue if langue and langue != 'auto' else 'fr'


def srt_film(pid):
    """Projet « sous-titres » : écrit « film.<langue>.srt » à côté de la vidéo (VLC, Infuse, Plex… le chargent
    tout seuls), ou dans le dossier d'export si la vidéo a été copiée ou que son dossier est en lecture seule.
    Réécrit le même fichier après des corrections ; ne remplace jamais un SRT qui n'est pas de Rature."""
    p = charger(pid)
    f_etat = chemin(pid, 'montage_etat.json')
    etat = json.load(open(f_etat)) if os.path.exists(f_etat) else {}
    jetons, srcs, supprimes, garde = donnees_montage(pid, etat)
    src = p['sources'][0]
    dossier_video = os.path.dirname(src['chemin'])
    if not src.get('copie') and os.path.isdir(dossier_video) and os.access(dossier_video, os.W_OK):
        dest = dossier_video
    else:
        dest = options_export()['dossier']
    langue = langue_transcrite(pid)
    cible = os.path.join(dest, f"{os.path.splitext(src['nom'])[0]}.{langue}.srt")
    if os.path.exists(cible) and cible != p.get('srt'):
        cible = nom_libre(cible)
    tmp = os.path.join(SUPPORT, 'tmp', f'srt-{pid}-{secrets.token_hex(3)}')
    os.makedirs(tmp)
    try:
        produit = ecrire_annexes(os.path.join(tmp, 'film'), jetons, srcs[:1], supprimes, garde, etat, {'srt_video': True})[0]
        shutil.move(produit, cible)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    EXPORTS_PRODUITS.add(cible)
    maj(pid, srt=cible, langue_detectee=langue)
    return {'fichiers': [cible], 'dossier': dest}


def ajouter_films(fichiers, langue='auto', copie=False):
    """Une vidéo = un projet « sous-titres », transcrit aussitôt ; renvoie les projets créés et les erreurs."""
    crees, erreurs = [], []
    for f in fichiers:
        try:
            sonder(f)                                     # format lisible ?
        except Exception as e:  # noqa: BLE001
            erreurs.append(str(e))
            if copie:
                os.remove(f)
            continue
        p = creer(os.path.splitext(os.path.basename(f))[0], 'sous-titres')
        maj(p['id'], langue=langue if langue in LANGUES_FILMS else 'auto')
        if copie:                                         # téléversé : rangé dans le projet (le son seul est gardé)
            dest = chemin(p['id'], 'sources', os.path.basename(f))
            shutil.move(f, dest)
            f = dest
        p, err = ajouter_sources(p['id'], [f], copie)
        src = p['sources'][0] if p['sources'] else {}
        if src.get('pistes') and langue != 'auto':            # plusieurs pistes (VF, VO…) : celle de la langue choisie
            k = next((i for i, x in enumerate(src['pistes']) if x['langue'] == langue), None)
            if k is not None and k != src.get('piste'):
                src['piste'] = k
                enregistrer(p)
        if err:
            erreurs += err
            shutil.rmtree(dossier(p['id']), ignore_errors=True)
            continue
        lancer(p['id'])
        crees.append(p['id'])
    return {'projets': crees, 'erreurs': erreurs}


LANGUES_FILMS = ('auto', 'fr', 'en', 'es', 'it', 'de', 'pt', 'nl')


def sous_titres(pid):
    """Sous-titres SRT directement depuis la transcription, sans export audio : calés sur l'enregistrement d'origine
    (un fichier par source quand le projet en réunit plusieurs), avec les corrections ; les mots coupés sont omis.
    Rangés dans le dossier d'export habituel (Téléchargements par défaut)."""
    p = charger(pid)
    if p['statut'] != 'pret':
        raise ValueError(_('pas_transcrit'))
    if p.get('genre') == 'sous-titres' and len(p['sources']) == 1:
        return srt_film(pid)
    f_etat = chemin(pid, 'montage_etat.json')
    etat = json.load(open(f_etat)) if os.path.exists(f_etat) else {}
    jetons, srcs, supprimes, garde = donnees_montage(pid, etat)
    dest = options_export()['dossier']
    tmp = os.path.join(SUPPORT, 'tmp', f'srt-{pid}-{secrets.token_hex(3)}')
    os.makedirs(tmp)
    try:
        base = os.path.join(tmp, slug(p['nom']))
        produits = ecrire_annexes(base, jetons, srcs, supprimes, garde, etat, {'srt_video': True})
        finaux = []
        for f in produits:
            nom = os.path.basename(f)
            if len(produits) == 1:                        # une seule source : « projet.srt »
                nom = slug(p['nom']) + '.srt'
            final = nom_libre(os.path.join(dest, nom))
            shutil.move(f, final)
            EXPORTS_PRODUITS.add(final)
            finaux.append(final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {'fichiers': finaux, 'dossier': dest}


def exporter(pid, etat, o, progression=lambda etape, pct: None):
    p = charger(pid)
    o = options_export(o)
    jetons, srcs, supprimes, garde = donnees_montage(pid, etat)
    if o['silences']:
        garde = raccourcir_silences(pid, garde)
    duree = sum(b - a for a, b in garde)
    nc = 1 if o['mono'] else max(1, max(int(s.get('ch') or 1) for s in p['sources']) if p['sources'] else 1)
    # espace disque : montage 24 bits + fichiers de traitement en float32
    besoin = duree * 48000 * nc * (3 + (16 if o['ameliorer'] else 2))
    libre = espace_libre(SUPPORT)
    if libre is not None and libre < besoin:
        raise IOError(_('espace', go=f'{libre / 1e9:.1f}'))

    nom = f'{slug(p["nom"])}_{datetime.now():%Y%m%d-%H%M}'
    tmp = os.path.join(SUPPORT, 'tmp', f'export-{pid}-{secrets.token_hex(3)}')
    os.makedirs(tmp)
    try:
        poids = {'montage': (0, 20 if o['ameliorer'] else 60), 'voix': (20, 80), 'encodage': (80 if o['ameliorer'] else 60, 97)}

        def prog(etape, pct):
            a, b = poids[etape]
            progression(etape, round(a + (b - a) * pct / 100))

        montage = os.path.join(tmp, 'montage.wav')
        rendre_montage(srcs, garde, montage, o['mono'], lambda f: prog('montage', 100 * f))
        dossier_sortie = os.path.join(tmp, nom)
        os.makedirs(dossier_sortie)
        audio = traiter_et_encoder(montage, os.path.join(dossier_sortie, nom), o, p['nom'], tmp, prog)
        fichiers = [audio] + ecrire_annexes(os.path.join(dossier_sortie, nom), jetons, srcs, supprimes, garde, etat, o['fichiers'])

        progression('archive', 98)
        dest = os.path.expanduser(o['dossier'])
        os.makedirs(dest, exist_ok=True)
        if o['zip']:
            import zipfile
            final = nom_libre(os.path.join(dest, nom + '.zip'))
            with zipfile.ZipFile(final, 'w') as z:
                for f in fichiers:
                    compression = zipfile.ZIP_STORED if f == audio else zipfile.ZIP_DEFLATED
                    z.write(f, os.path.basename(f), compress_type=compression)
        elif len(fichiers) == 1:
            final = nom_libre(os.path.join(dest, os.path.basename(audio)))
            shutil.move(audio, final)
        else:
            final = nom_libre(os.path.join(dest, nom))
            shutil.move(dossier_sortie, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    EXPORTS_PRODUITS.add(final)
    with verrou:
        p = charger(pid)
        p.setdefault('exports', []).append({'chemin': final, 'date': datetime.now().isoformat(timespec='seconds'),
                                            'format': o['format']})
        enregistrer(p)
    sauver_reglages(export={k: o[k] for k in EXPORT_DEFAUT})
    progression('fini', 100)
    return {'chemin': final, 'nom': os.path.basename(final), 'taille': os.path.getsize(final) if os.path.isfile(final) else None,
            'duree': duree, 'raccords': len(garde) - 1}


file_exports = queue.Queue()


def ouvrier_exports():
    """Les exports passent un par un, sans bloquer l'interface ni la transcription."""
    while True:
        tid, pid, etat, o = file_exports.get()
        t = TACHES[tid]
        t.update(etat='en_cours', debut=time.time())
        try:
            t['resultat'] = exporter(pid, etat, o, lambda e, pct: t.update(etape=e, progression=pct))
            t['etat'] = 'fini'
        except Exception as e:  # noqa: BLE001
            print('Erreur d\'export', e, file=sys.stderr)
            t.update(etat='erreur', erreur=str(e))
        t['fin'] = time.time()


def lancer_export(pid, etat, o):
    tid = secrets.token_hex(4)
    o = options_export(o)
    TACHES[tid] = {'id': tid, 'pid': pid, 'nom': charger(pid)['nom'], 'format': o['format'], 'etat': 'attente',
                   'etape': 'montage', 'progression': 0, 'cree': time.time(), 'vu': False}
    file_exports.put((tid, pid, etat, o))
    return tid


def liste_taches():
    """Exports de cette session : en cours, en attente, puis terminés (jusqu'à ce qu'ils soient masqués)."""
    ordre = {'en_cours': 0, 'attente': 1, 'erreur': 2, 'fini': 3}
    return sorted((t for t in TACHES.values() if not t.get('vu')), key=lambda t: (ordre.get(t['etat'], 9), -t['cree']))


def apercu_export(pid, etat, o, position, duree=20.0):
    """Traite 20 s du montage à partir de `position` (temps des sources) : fichiers « avant » et « après »."""
    o = options_export(o)
    jetons, srcs, supprimes, garde = donnees_montage(pid, etat)
    if o['silences']:
        garde = raccourcir_silences(pid, garde)
    morceaux, reste = [], duree
    for a, b in garde:
        if b <= position:
            continue
        a = max(a, position)
        morceaux.append([a, min(b, a + reste)])
        reste -= morceaux[-1][1] - a
        if reste <= 0.05:
            break
    if not morceaux:                                     # curseur en fin de montage : prendre le début
        return apercu_export(pid, etat, o, 0.0, duree)
    d = os.path.join(SUPPORT, 'tmp', f'apercu-{pid}')
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    montage = os.path.join(d, 'montage.wav')
    rendre_montage(srcs, morceaux, montage, o['mono'])
    avant = os.path.join(d, 'avant.wav')
    afconvert('-f', 'WAVE', '-d', 'LEI16', montage, avant)
    apres = traiter_et_encoder(montage, os.path.join(d, 'apres'), o, 'apercu', d, lambda e, p: None)
    jeton = secrets.token_hex(3)
    return {'avant': f'/p/{pid}/apercu-export/avant.wav?{jeton}',
            'apres': f'/p/{pid}/apercu-export/{os.path.basename(apres)}?{jeton}'}


# ---------------------------------------------------------------- voix claire
# Écoute améliorée : une copie de l'aperçu traitée une fois (égaliseur voix, nivellement, −16 LUFS, limiteur),
# en mono 16 kHz. Le lecteur la lit directement : pas de Web Audio, dont WebKit coupe le son après un saut
# quand la lecture est accélérée.

VOIX_CLAIRE = {}                  # pid → {'etat': 'en_cours'|'erreur', 'pct', 'erreur'}


def voix_claire_prete(pid):
    f, src = chemin(pid, 'apercu_clair.m4a'), chemin(pid, 'apercu.m4a')
    return os.path.exists(f) and os.path.getmtime(f) >= os.path.getmtime(src)


def etat_voix_claire(pid):
    if voix_claire_prete(pid):
        return {'etat': 'pret', 'url': f'/p/{pid}/apercu_clair.m4a?{int(os.path.getmtime(chemin(pid, "apercu_clair.m4a")))}'}
    return VOIX_CLAIRE.get(pid) or {'etat': 'absent'}


def preparer_voix_claire(pid):
    import traitement
    d = os.path.join(SUPPORT, 'tmp', f'voix-{pid}')
    t = VOIX_CLAIRE[pid]
    try:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d)
        brut = os.path.join(d, 'brut.wav')
        afconvert('-f', 'WAVE', '-d', 'LEI16@16000', '-c', '1', chemin(pid, 'apercu.m4a'), brut)
        t['pct'] = 10
        etapes = {'traitement': lambda p: 10 + p * 0.75,       # 0–60 → 10–55 %
                  'ecriture': lambda p: 55 + p * 0.25}         # 0–100 → 55–80 %

        def progression(etape, pct):
            if etape in etapes:
                t['pct'] = int(etapes[etape](pct))
        clair = os.path.join(d, 'clair.wav')
        traitement.ameliorer(brut, clair, {'bruit': 'aucun', 'egaliseur': True, 'nivellement': True,
                                           'cible_lufs': -16.0, 'limiteur': True, 'bits': 16}, {}, d, progression)
        os.remove(brut)
        t['pct'] = 80
        afconvert('-f', 'm4af', '-d', 'aac', '-b', '48000', clair, os.path.join(d, 'clair.m4a'))
        os.replace(os.path.join(d, 'clair.m4a'), chemin(pid, 'apercu_clair.m4a'))
        VOIX_CLAIRE.pop(pid, None)
    except Exception as e:  # noqa: BLE001
        VOIX_CLAIRE[pid] = {'etat': 'erreur', 'erreur': str(e)}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def lancer_voix_claire(pid):
    with verrou:
        if not voix_claire_prete(pid) and VOIX_CLAIRE.get(pid, {}).get('etat') != 'en_cours':
            VOIX_CLAIRE[pid] = {'etat': 'en_cours', 'pct': 0}
            threading.Thread(target=preparer_voix_claire, args=(pid,), daemon=True).start()
    return etat_voix_claire(pid)


# --------------------------------------------------------------------- serveur

class Gestionnaire(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEB, **k)

    def log_message(self, fmt, *args):
        if '/api/' in self.path and 'GET' not in (args[0] if args else ''):
            sys.stderr.write('%s\n' % (fmt % args))

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def json(self, obj, code=200):
        corps = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def corps(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n) or b'{}')

    def route(self):
        u = urlparse(self.path)
        return [unquote(x) for x in u.path.strip('/').split('/') if x], parse_qs(u.query)

    def do_GET(self):
        try:
            seg, q = self.route()
            if not seg:
                self.path = '/choix.html'                     # au lancement : montage ou sous-titres de film
                return super().do_GET()
            if seg == ['projets']:
                self.path = '/accueil.html'
                return super().do_GET()
            if seg == ['sous-titres']:
                self.path = '/soustitres.html'
                return super().do_GET()
            if seg in (['reglages'], ['licences']):
                self.path = f'/{seg[0]}.html'
                return super().do_GET()
            if seg[0] == 'p' and len(seg) == 2:
                p = charger(seg[1])
                self.path = '/editeur.html' if p['statut'] == 'pret' else '/preparation.html'
                return super().do_GET()
            if seg[0] == 'p' and len(seg) == 4 and seg[2] == 'apercu-export':
                f = os.path.join(SUPPORT, 'tmp', f'apercu-{seg[1]}', os.path.basename(seg[3]))
                types = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.m4a': 'audio/mp4'}
                return self.envoyer_fichier(f, types.get(os.path.splitext(f)[1], 'application/octet-stream'))
            if seg == ['api', 'taches']:
                return self.json(liste_taches())
            if seg[:2] == ['api', 'taches'] and len(seg) == 3:
                t = TACHES.get(seg[2])
                return self.json(t if t else {'etat': 'inconnue'})
            if seg[0] == 'p' and len(seg) == 3 and seg[2] in ('apercu.m4a', 'apercu_clair.m4a'):
                if seg[2] == 'apercu.m4a' and not os.path.exists(chemin(seg[1], 'apercu.m4a')) \
                        and charger(seg[1]).get('genre') == 'sous-titres':
                    apercu_film(seg[1])                  # film : l'aperçu n'est fait qu'à la première écoute
                return self.envoyer_fichier(chemin(seg[1], seg[2]), 'audio/mp4')
            if seg[:2] == ['api', 'projets']:
                return self.json(lister())
            if seg[:2] == ['api', 'systeme']:
                return self.json({'version': VERSION, 'modele': etat_modele(), 'whisper': bool(binaire_whisper()),
                                  'donnees': projets_dir()})
            if seg[:2] == ['api', 'maj']:
                forcer = 'forcer' in q
                if not forcer and not reglages().get('maj_auto', True):
                    return self.json({'actuelle': VERSION, 'disponible': False, 'auto': False})
                return self.json(verifier_maj(forcer))
            if seg[:2] == ['api', 'reglages']:
                return self.json(etat_reglages())
            if seg == ['langue.js']:
                corps = f'window.LANGUE = {json.dumps(langue_ui())};'.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/javascript; charset=utf-8')
                self.send_header('Content-Length', str(len(corps)))
                self.end_headers()
                return self.wfile.write(corps)
            if seg[:2] == ['api', 'licences']:
                f = os.path.join(WEB, 'licences.json')
                return self.json(json.load(open(f)) if os.path.exists(f) else [])
            if seg[0] == 'api' and seg[1] == 'p':
                pid, quoi = seg[2], (seg[3] if len(seg) > 3 else '')
                if quoi == '':
                    p = charger(pid)
                    return self.json({**p, **resume(p)})
                if quoi == 'export-options':
                    p = charger(pid)
                    o = options_export()
                    return self.json({'options': o, 'defaut': EXPORT_DEFAUT, 'telechargements': DOSSIER_TELECHARGEMENTS,
                                      'canaux_source': max([int(x.get('ch') or 1) for x in p['sources']] or [1]),
                                      'outils': {'lame': bool(outil('lame')), 'rnnoise': bool(outil('rnnoise'))}})
                if quoi == 'mots':
                    return self.envoyer_fichier(chemin(pid, 'mots.json'), 'application/json')
                if quoi == 'voix-claire':
                    return self.json(etat_voix_claire(pid))
                if quoi == 'etat':
                    f = chemin(pid, 'montage_etat.json')
                    return self.json(json.load(open(f)) if os.path.exists(f) else {})
                if quoi == 'crete':
                    f = chemin(pid, 'crete.json')
                    c = json.load(open(f)) if os.path.exists(f) else []
                    n = int(q.get('n', [0])[0] or 0)
                    if n and c:
                        b = np.linspace(0, len(c), n + 1).astype(int)
                        c = [max(c[x:y]) if y > x else 0 for x, y in zip(b, b[1:])]
                    return self.json(c)
            return super().do_GET()
        except FileNotFoundError:
            self.send_error(404)
        except Exception as e:  # noqa: BLE001
            return self.json({'erreur': str(e)}, 500)

    def do_PUT(self):
        try:
            seg, q = self.route()
            if seg == ['api', 'films', 'televerser']:          # hors de l'app : la vidéo est envoyée puis réduite au son
                nom = os.path.basename(q['nom'][0])
                if not nom.lower().endswith(EXT_AUDIO):
                    return self.json({'erreur': _('format_inconnu', nom=nom)}, 400)
                d = os.path.join(SUPPORT, 'tmp', f'film-{secrets.token_hex(3)}')
                os.makedirs(d)
                dest = os.path.join(d, nom)
                reste = int(self.headers['Content-Length'])
                with open(dest, 'wb') as f:
                    while reste > 0:
                        bloc = self.rfile.read(min(1 << 20, reste))
                        if not bloc:
                            break
                        f.write(bloc)
                        reste -= len(bloc)
                try:
                    if reste:
                        return self.json({'erreur': _('envoi_interrompu')}, 400)
                    return self.json(ajouter_films([dest], (q.get('langue') or ['auto'])[0], copie=True))
                finally:
                    shutil.rmtree(d, ignore_errors=True)
            if seg[:2] == ['api', 'p'] and seg[3] == 'televerser':
                pid = seg[2]
                nom = os.path.basename(q['nom'][0])
                if not nom.lower().endswith(EXT_AUDIO):
                    return self.json({'erreur': _('format_inconnu', nom=nom)}, 400)
                dest = chemin(pid, 'sources', nom)
                base, ext = os.path.splitext(dest)
                i = 2
                while os.path.exists(dest):
                    dest = f'{base} ({i}){ext}'
                    i += 1
                reste = int(self.headers['Content-Length'])
                with open(dest, 'wb') as f:
                    while reste > 0:
                        bloc = self.rfile.read(min(1 << 20, reste))
                        if not bloc:
                            break
                        f.write(bloc)
                        reste -= len(bloc)
                if reste:
                    os.remove(dest)
                    return self.json({'erreur': _('envoi_interrompu')}, 400)
                p, err = ajouter_sources(pid, [dest], copie=True)
                return self.json({'projet': p, 'erreurs': err})
        except Exception as e:  # noqa: BLE001
            return self.json({'erreur': str(e)}, 500)
        self.send_error(404)

    def do_POST(self):
        try:
            seg, _ = self.route()
            if seg == ['api', 'projets']:
                return self.json(creer(self.corps().get('nom', '')))
            if seg == ['api', 'films']:                       # chemins connus (glisser-déposer dans l'app)
                c = self.corps()
                return self.json(ajouter_films(c.get('chemins') or [], c.get('langue') or 'auto'))
            if seg == ['api', 'films', 'choisir']:
                fichiers = choisir_dans_finder()
                return self.json(ajouter_films(fichiers, self.corps().get('langue') or 'auto') if fichiers else {'projets': [], 'erreurs': []})
            if seg == ['api', 'reglages', 'langue-films']:
                choix = self.corps().get('langue')
                sauver_reglages(langue_films=choix if choix in LANGUES_FILMS else 'auto')
                return self.json({'ok': True})
            if seg == ['api', 'modele']:
                telecharger_modele()
                return self.json(etat_modele())
            if seg[:2] == ['api', 'modeles'] and len(seg) == 4:
                cle, action = seg[2], seg[3]
                if cle not in MODELES:
                    raise ValueError(_('modele_inconnu'))
                if action == 'telecharger':
                    telecharger_modele(cle)
                elif action == 'annuler' and cle in telechargements:
                    telechargements[cle]['etat'] = 'annule'
                elif action == 'supprimer':
                    supprimer_modele(cle)
                elif action == 'importer':
                    importer_modele(cle)
                elif action == 'utiliser':
                    sauver_reglages(modele=cle)
                    if not chemin_modele(cle):
                        telecharger_modele(cle)
                return self.json(etat_reglages())
            if seg == ['api', 'reglages', 'dossier']:
                c = self.corps()
                cle = c['cle']
                if cle not in ('dossier_projets', 'dossier_modeles'):
                    raise ValueError(_('reglage_inconnu'))
                n = changer_dossier(cle, c['chemin'], c.get('deplacer', False))
                return self.json({**etat_reglages(), 'deplaces': n})
            if seg == ['api', 'reglages', 'maj-auto']:
                sauver_reglages(maj_auto=bool(self.corps().get('maj_auto')))
                return self.json(etat_reglages())
            if seg == ['api', 'reglages', 'langue']:
                choix = self.corps().get('langue_ui')
                sauver_reglages(langue_ui=choix if choix in ('auto', 'fr', 'en') else 'auto')
                return self.json(etat_reglages())
            if seg[:2] == ['api', 'taches'] and len(seg) == 4 and seg[3] == 'masquer':
                if seg[2] in TACHES and TACHES[seg[2]]['etat'] in ('fini', 'erreur'):
                    TACHES[seg[2]]['vu'] = True
                return self.json(liste_taches())
            if seg == ['api', 'choisir-dossier']:
                return self.json({'chemin': choisir_dossier()})
            if seg == ['api', 'ouvrir-dossier']:
                quoi = self.corps().get('quoi')
                d = reglages().get(quoi) if quoi in ('dossier_projets', 'dossier_modeles') else None
                if d:
                    os.makedirs(d, exist_ok=True)
                    subprocess.run(['open', d])
                return self.json({'ok': True})
            if seg == ['api', 'dossier-donnees']:
                subprocess.run(['open', projets_dir()])
                return self.json({'ok': True})
            if seg[:2] == ['api', 'p']:
                pid, quoi = seg[2], seg[3]
                if quoi == 'etat':
                    tmp = chemin(pid, 'montage_etat.json.tmp')
                    json.dump(self.corps(), open(tmp, 'w'), ensure_ascii=False)
                    os.replace(tmp, chemin(pid, 'montage_etat.json'))
                    maj(pid)
                    return self.json({'ok': True, 'heure': time.strftime('%H:%M')})
                if quoi == 'voix-claire':
                    return self.json(lancer_voix_claire(pid))
                if quoi == 'sous-titres':
                    return self.json(sous_titres(pid))
                if quoi == 'export':
                    c = self.corps()
                    return self.json({'tache': lancer_export(pid, c.get('etat') or {}, c.get('options'))})
                if quoi == 'apercu-export':
                    c = self.corps()
                    return self.json(apercu_export(pid, c.get('etat') or {}, c.get('options'), float(c.get('position') or 0)))
                if quoi == 'maj':
                    c = self.corps()
                    p = charger(pid)
                    for k in ('nom', 'langue', 'vocabulaire', 'verbatim'):
                        if k in c:
                            p[k] = c[k]
                    if 'ordre' in c and p['statut'] in ('brouillon', 'erreur'):
                        p['sources'] = [p['sources'][i] for i in c['ordre']]
                    enregistrer(p)
                    return self.json(p)
                if quoi == 'retirer':
                    p = charger(pid)
                    if p['statut'] not in ('brouillon', 'erreur'):
                        raise ValueError(_('deja_lance_court'))
                    s = p['sources'].pop(self.corps()['index'])
                    if s.get('copie') and os.path.exists(resoudre(pid, s['chemin'])):
                        os.remove(resoudre(pid, s['chemin']))
                    enregistrer(p)
                    return self.json(p)
                if quoi == 'choisir':
                    fichiers = choisir_dans_finder()
                    if not fichiers:
                        return self.json({'projet': charger(pid), 'erreurs': []})
                    p, err = ajouter_sources(pid, fichiers, copie=False)
                    return self.json({'projet': p, 'erreurs': err})
                if quoi == 'lancer':
                    return self.json(lancer(pid))
                if quoi == 'supprimer':
                    supprimer(pid)
                    return self.json({'ok': True})
                if quoi == 'finder':
                    cible = self.corps().get('fichier') or dossier(pid)
                    if not os.path.abspath(cible).startswith(dossier(pid)) and cible not in EXPORTS_PRODUITS \
                            and cible != charger(pid).get('srt'):
                        raise ValueError(_('chemin_hors_projet'))
                    subprocess.run(['open', '-R', cible])
                    return self.json({'ok': True})
        except Exception as e:  # noqa: BLE001
            return self.json({'erreur': str(e)}, 500)
        self.send_error(404)

    def envoyer_fichier(self, f, type_mime):
        """Envoi avec prise en charge des requêtes Range (indispensable pour se déplacer dans l'audio)."""
        taille = os.path.getsize(f)
        plage = self.headers.get('Range')
        debut, fin = 0, taille - 1
        if plage:
            m = re.match(r'bytes=(\d*)-(\d*)', plage)
            if m.group(1):
                debut = int(m.group(1))
                if m.group(2):
                    fin = min(int(m.group(2)), taille - 1)
            elif m.group(2):
                debut = taille - int(m.group(2))
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {debut}-{fin}/{taille}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', type_mime)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(fin - debut + 1))
        self.end_headers()
        with open(f, 'rb') as fh:
            fh.seek(debut)
            reste = fin - debut + 1
            try:
                while reste > 0:
                    bloc = fh.read(min(1 << 20, reste))
                    if not bloc:
                        break
                    self.wfile.write(bloc)
                    reste -= len(bloc)
            except (BrokenPipeError, ConnectionResetError):
                pass


def reprendre():
    """Relance les traitements interrompus par un arrêt du serveur."""
    for r in lister():
        if r['statut'] in ('attente', 'traitement'):
            maj(r['id'], statut='attente', etape=None, progression=0, message='')
            file_travaux.put(r['id'])


def demarrer(port=PORT):
    """Lance le serveur et le traitement en tâche de fond ; renvoie le port utilisé."""
    os.makedirs(projets_dir(), exist_ok=True)
    shutil.rmtree(os.path.join(SUPPORT, 'tmp'), ignore_errors=True)    # restes d'un export interrompu
    threading.Thread(target=ouvrier, daemon=True).start()
    threading.Thread(target=ouvrier_exports, daemon=True).start()
    reprendre()
    if not chemin_modele():
        telecharger_modele()
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Gestionnaire)
    except OSError:                      # port déjà pris : un autre libre
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Gestionnaire)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd.server_address[1]


if __name__ == '__main__':
    print(f'Rature : http://127.0.0.1:{demarrer()}')
    threading.Event().wait()
