"""Entretien fictif lu par les voix de synthèse de macOS : sert aux captures d'écran du site (aucune donnée réelle)."""
import os, subprocess, wave

TOURS = [
 ('j', "Bonjour Claire. Vous restaurez depuis trois ans le moulin de la Doue, au bord de l'Ouche. Comment est-ce que tout a commencé ?"),
 ('c', "Alors, euh, tout a commencé un peu par hasard, en fait. J'ai vu une petite annonce, le moulin était à vendre, et le toit, le toit était en train de s'effondrer."),
 ('c', "Personne n'en voulait. Moi, je suis tombée amoureuse de la roue. Une roue en chêne de cinq mètres de diamètre, qui n'avait pas tourné depuis mille neuf cent soixante-deux."),
 ('j', "Et vous saviez faire tourner un moulin ?"),
 ('c', "Pas du tout ! Euh, non, vraiment pas. J'étais, euh, j'étais comptable à Dijon. J'ai tout appris avec un ancien meunier du village, qui avait quatre-vingt-sept ans."),
 ('c', "Il venait tous les matins avec son café, il s'asseyait sur un sac de farine, et il m'expliquait. Le plus beau souvenir de ces trois années, c'est le jour où la roue a refait son premier tour. Il a pleuré, et moi aussi."),
 ('j', "Aujourd'hui, le moulin produit de la farine ?"),
 ('c', "Oui, de la farine de petit épeautre, et un peu de seigle. On travaille avec six paysans du plateau. Tout est moulu à la meule de pierre, lentement, euh, sans chauffer le grain."),
 ('c', "Et le mercredi, on ouvre aux écoles. Les enfants mettent les mains dans la farine encore tiède. C'est, c'est ce que je préfère."),
 ('j', "Quels sont vos projets pour la suite ?"),
 ('c', "Euh, la grande question, c'est l'énergie. La roue pourrait produire de l'électricité pour tout le hameau. On a déposé un dossier, on attend la réponse de la préfecture. Si ça marche, le moulin redeviendra le cœur du village, comme autrefois."),
]
VOIX = {'j': 'Thomas', 'c': 'Flo (Français (France))'}
SR = 22050

def lire(texte, voix, tmp):
    subprocess.run(['say', '-v', voix, '-r', '178', '--file-format=WAVE', '--data-format=LEI16@22050', '-o', tmp, texte], check=True)
    with wave.open(tmp) as w:
        return w.readframes(w.getnframes())

def ecrire(nom, tours):
    blanc = lambda s: b'\0\0' * int(SR * s)
    with wave.open(os.path.join('audio', nom), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(blanc(0.8))
        for k, (qui, texte) in enumerate(tours):
            w.writeframes(lire(texte, VOIX[qui], os.path.join('audio', '_tour.tmp.wav')))
            w.writeframes(blanc(0.9 if k + 1 < len(tours) and tours[k + 1][0] != qui else 0.5))
        w.writeframes(blanc(1.0))

ecrire('MOULIN_01_20260912_101500.wav', TOURS[:6])
ecrire('MOULIN_02_20260912_103800.wav', TOURS[6:])
print('ok')
