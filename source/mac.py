"""Rature.app — fenêtre Mac native autour du serveur local de Rature."""
import os

import AppKit
import objc
import webview
from PyObjCTools import AppHelper
from webview.menu import Menu, MenuAction
from webview.dom import DOMEventHandler
from webview.platforms.cocoa import BrowserView

import rature



# pywebview intercepte ⌘Z au niveau natif : on le laisse passer à la page, qui gère
# l'annulation des coupes (⌘Z) et le rétablissement (⇧⌘Z).
_touche_origine = BrowserView.WebKitHost.keyDown_.callable


def keyDown_(self, event):
    if event.modifierFlags() & AppKit.NSEventModifierFlagCommand and event.charactersIgnoringModifiers().lower() == 'z':
        objc.super(BrowserView.WebKitHost, self).keyDown_(event)
    else:
        _touche_origine(self, event)


objc.classAddMethods(BrowserView.WebKitHost, [keyDown_])


# Menus standard de pywebview (anglais par défaut)
MENUS_FR = {
    'global.quitConfirmation': 'Voulez-vous vraiment quitter ?', 'global.ok': 'OK', 'global.quit': 'Quitter',
    'global.cancel': 'Annuler', 'global.saveFile': 'Enregistrer le fichier',
    'cocoa.menu.about': 'À propos de', 'cocoa.menu.services': 'Services', 'cocoa.menu.view': 'Présentation',
    'cocoa.menu.edit': 'Édition', 'cocoa.menu.hide': 'Masquer', 'cocoa.menu.hideOthers': 'Masquer les autres',
    'cocoa.menu.showAll': 'Tout afficher', 'cocoa.menu.quit': 'Quitter', 'cocoa.menu.fullscreen': 'Passer en mode plein écran',
    'cocoa.menu.cut': 'Couper', 'cocoa.menu.copy': 'Copier', 'cocoa.menu.paste': 'Coller', 'cocoa.menu.selectAll': 'Tout sélectionner',
}


def raccourci_reglages():
    """Ajoute ⌘, à l'entrée « Réglages… » du menu Rature (pywebview ne gère pas les raccourcis de menu)."""
    def poser():
        menu = AppKit.NSApp.mainMenu()
        app = menu.itemAtIndex_(0).submenu() if menu and menu.numberOfItems() else None
        for i in range(app.numberOfItems() if app else 0):
            item = app.itemAtIndex_(i)
            if item.title() == rature._('menu_reglages'):
                item.setKeyEquivalent_(',')
                item.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagCommand)
    AppHelper.callAfter(poser)


def journal():
    """Erreurs de l'app dans ~/Library/Logs/Rature.log (visible dans Console) : sans terminal, elles seraient perdues."""
    import sys
    import time
    f = os.path.expanduser('~/Library/Logs/Rature.log')
    try:
        os.makedirs(os.path.dirname(f), exist_ok=True)
        if os.path.exists(f) and os.path.getsize(f) > 2_000_000:
            os.replace(f, f + '.1')
        sortie = open(f, 'a', buffering=1, encoding='utf-8', errors='replace')
    except OSError:
        return
    sys.stdout = sys.stderr = sortie
    print(f'--- Rature {rature.VERSION}, lancé le {time.strftime("%Y-%m-%d %H:%M:%S")}')


def main():
    journal()
    port = rature.demarrer()
    base = f'http://127.0.0.1:{port}'
    _ = rature._
    menu = [Menu('__app__', [
        MenuAction(_('menu_reglages'), lambda: fenetre.load_url(base + '/reglages')),
        MenuAction(_('menu_licences'), lambda: fenetre.load_url(base + '/licences')),
    ])]
    types_audio = (f"{_('fichiers_audio')} (" + ';'.join('*' + e for e in rature.EXT_AUDIO) + ')', f"{_('tous_fichiers')} (*.*)")
    fenetre = webview.create_window(
        'Rature', f'http://127.0.0.1:{port}/',
        width=1320, height=900, min_size=(960, 640),
        background_color='#F5F5F2',
        text_select=True,          # indispensable : on monte en sélectionnant le texte
        menu=menu,
    )

    def choisir():
        r = fenetre.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=types_audio)
        return list(r or [])

    def choisir_dossier():
        r = fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None

    rature.CHOISIR_FICHIERS = choisir
    rature.CHOISIR_DOSSIER = choisir_dossier

    # Sous-titres de film : les vidéos glissées sur la fenêtre arrivent avec leur chemin complet (pas de copie,
    # le .srt est écrit à côté). Un dossier déposé apporte les vidéos qu'il contient.
    def deposer_films(evenement):
        import json
        import traceback
        try:
            chemins = []
            for f in (evenement.get('dataTransfer') or {}).get('files') or []:
                c = f.get('pywebviewFullPath')
                if c and os.path.isdir(c):
                    chemins += sorted(os.path.join(c, x) for x in os.listdir(c)
                                      if x.lower().endswith(rature.EXT_AUDIO + ('.mkv', '.avi', '.webm')) and not x.startswith('.'))
                elif c:
                    chemins.append(c)
            if chemins:
                r = rature.ajouter_films(chemins, rature.reglages().get('langue_films', 'auto'))
                fenetre.evaluate_js(f'window.filmsDeposes && window.filmsDeposes({json.dumps(r)})')
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    def page_chargee():
        try:
            if '/sous-titres' in (fenetre.get_current_url() or ''):
                fenetre.dom.document.on('drop', DOMEventHandler(deposer_films, prevent_default=True))
        except Exception:  # noqa: BLE001
            import traceback
            traceback.print_exc()

    fenetre.events.loaded += page_chargee
    try:
        webview.start(raccourci_reglages, private_mode=False, storage_path=os.path.join(rature.SUPPORT, 'webview'),
                      localization=MENUS_FR if rature.langue_ui() == 'fr' else {})
    finally:
        rature.arreter()        # une transcription interrompue reprendra au prochain lancement


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--cohere':       # sous-processus de transcription Cohere (MLX)
        import cohere_worker
        sys.exit(cohere_worker.main(sys.argv[2:]))
    main()
