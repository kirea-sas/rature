"""Aligne un texte sans timestamps (Cohere Transcribe) sur les mots horodatés d'une autre transcription (Whisper).

Les mots identiques servent d'ancres et reprennent les temps de Whisper ; entre deux ancres, les mots différents
se partagent le temps disponible au prorata de leur longueur. Résultat : des mots horodatés au format
de whisper.cpp ({'offsets': {'from', 'to'}, 'text'}), utilisables tels quels par la préparation de Rature.
"""
import difflib
import re
import unicodedata


def normaliser(mot):
    m = unicodedata.normalize('NFKD', mot.lower().replace('’', "'"))
    m = ''.join(c for c in m if not unicodedata.combining(c))
    return re.sub(r"[^\w']+", '', m)


def repartir(mots, t0, t1):
    """Répartit des mots sur [t0, t1] au prorata de leur longueur (au moins 40 ms chacun)."""
    if not mots:
        return []
    t1 = max(t1, t0 + 0.04 * len(mots))
    poids = [max(1, len(m)) for m in mots]
    total, t, sortie = sum(poids), t0, []
    for m, p in zip(mots, poids):
        d = (t1 - t0) * p / total
        sortie.append((m, t, t + d))
        t += d
    return sortie


def aligner(whisper, texte_cohere):
    """whisper : liste de (mot, t0, t1) ; renvoie la liste des mots de Cohere horodatés (mot, t0, t1)."""
    cohere = texte_cohere.split()
    if not whisper or not cohere:
        return [(m, t0, t1) for m, t0, t1 in whisper]
    a = [normaliser(m) for m, _, _ in whisper]
    b = [normaliser(m) for m in cohere]
    sortie = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal':
            sortie += [(cohere[j1 + k], whisper[i1 + k][1], whisper[i1 + k][2]) for k in range(i2 - i1)]
        elif op == 'replace':                  # mots différents : le temps des mots Whisper remplacés
            sortie += repartir(cohere[j1:j2], whisper[i1][1], whisper[i2 - 1][2])
        elif op == 'insert':                   # mots en plus : partagés avec le mot précédent
            if sortie:
                m, t0, t1 = sortie.pop()
                fin = whisper[i1][1] if i1 < len(whisper) else t1
                sortie += repartir([m] + cohere[j1:j2], t0, max(t1, fin))
            else:
                sortie += repartir(cohere[j1:j2], whisper[0][1], whisper[0][1] + 0.3 * (j2 - j1))
        # 'delete' : mots de Whisper absents chez Cohere, leur temps revient aux voisins
    # garantir des temps croissants
    propre, dernier = [], 0.0
    for m, t0, t1 in sortie:
        t0 = max(t0, dernier)
        t1 = max(t1, t0 + 0.03)
        propre.append((m, t0, t1))
        dernier = t0 + 0.01
    return propre


def repartir_sur_la_voix(mots, a, b, voix, pas):
    """Répartit les mots sur les moments de parole de [a, b] (masque `voix` par blocs de `pas` s)."""
    i0, i1 = int(a / pas), max(int(a / pas) + 1, int(b / pas))
    actifs = [i for i in range(i0, min(i1, len(voix))) if voix[i]]
    if len(actifs) < max(5, len(mots)):                   # trop peu de parole détectée : répartition uniforme
        return repartir(mots, a, b)
    poids = [max(1, len(m)) for m in mots]
    total, cumul, sortie = sum(poids), 0, []
    for m, p in zip(mots, poids):
        k0 = actifs[min(len(actifs) - 1, int(len(actifs) * cumul / total))]
        cumul += p
        k1 = actifs[min(len(actifs) - 1, max(0, int(len(actifs) * cumul / total) - 1))]
        sortie.append((m, k0 * pas, max(k1 + 1, k0 + 1) * pas))
    return sortie


def aligner_par_morceaux(whisper, morceaux, voix=None, pas=0.01):
    """Aligne morceau par morceau (morceaux = [{'a', 'b', 'texte'}]) : les temps restent dans les bornes de chaque
    morceau. Si Whisper ne correspond pas au texte du morceau (Whisper a décroché, ou a halluciné), les mots sont
    répartis sur les moments de parole détectés dans le son (`voix`, un booléen par bloc de `pas` s)."""
    sortie, i = [], 0
    for k, mc in enumerate(morceaux):
        a, b = mc['a'], mc['b']
        j = i
        while j < len(whisper) and whisper[j][1] < b:
            j += 1
        dans = [w for w in whisper[i:j] if w[1] >= a - 0.5]
        texte = mc['texte'].split()
        i = j
        if not texte:
            continue
        accord = difflib.SequenceMatcher(None, [normaliser(w[0]) for w in dans], [normaliser(m) for m in texte],
                                         autojunk=False).ratio() if dans else 0.0
        if accord >= 0.5:
            mots = aligner(dans, mc['texte'])
        elif voix is not None:
            mots = repartir_sur_la_voix(texte, a, b, voix, pas)
        else:
            mots = repartir(texte, a, b)
        sortie += [(m, min(max(t0, a), b), min(max(t1, a), b)) for m, t0, t1 in mots]
    # temps croissants sans dérive : on ne repousse que de quelques millisecondes
    propre, dernier = [], 0.0
    for m, t0, t1 in sortie:
        t0 = max(t0, dernier)
        propre.append((m, t0, max(t1, t0 + 0.02)))
        dernier = t0 + 0.001
    return propre


def vers_whisper_json(mots):
    return {'transcription': [{'offsets': {'from': int(round(t0 * 1000)), 'to': int(round(t1 * 1000))}, 'text': ' ' + m}
                              for m, t0, t1 in mots]}
