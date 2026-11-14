"""Captures d'écran des pages de Rature avec WebKit (le moteur de l'app), en clair ou en sombre.

Usage : python capturer.py <url> <sortie.png> <largeur> <hauteur> [clair|sombre] [attente_s]
"""
import sys

import AppKit
import Foundation
import objc
import WebKit
from PyObjCTools import AppHelper

url, sortie, L, H = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
theme = sys.argv[5] if len(sys.argv) > 5 else 'clair'
attente = float(sys.argv[6]) if len(sys.argv) > 6 else 2.5

app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
fen = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((-10000, -10000), (L, H)), AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
fen.setAppearance_(AppKit.NSAppearance.appearanceNamed_(
    AppKit.NSAppearanceNameDarkAqua if theme == 'sombre' else AppKit.NSAppearanceNameAqua))
vue = WebKit.WKWebView.alloc().initWithFrame_configuration_(((0, 0), (L, H)), WebKit.WKWebViewConfiguration.alloc().init())
fen.setContentView_(vue)
fen.orderFront_(None)


def enregistrer(image, erreur):
    if image is None:
        print('échec', erreur)
    else:
        rep = AppKit.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(sortie, True)
        print('ok', sortie, int(rep.pixelsWide()), 'x', int(rep.pixelsHigh()))
    AppHelper.stopEventLoop()


def capturer():
    conf = WebKit.WKSnapshotConfiguration.alloc().init()
    conf.setSnapshotWidth_(L * 2)          # image Retina (2×)
    vue.takeSnapshotWithConfiguration_completionHandler_(conf, enregistrer)


# Fenêtre hors écran : WebKit y suspend animations et requestAnimationFrame. On fige donc les transitions
# et on dessine directement la mini-carte avant la capture.
PREPARER = '''
  const st = document.createElement('style');
  st.textContent = '*, *::before, *::after { transition: none !important; animation: none !important; }';
  document.head.appendChild(st);
  if (typeof mesurerMinimap === 'function' && typeof spans !== 'undefined' && spans.length) { mesurerMinimap(); dessinerMinimap(); }
  true;
'''


def preparer():
    vue.evaluateJavaScript_completionHandler_(PREPARER, lambda r, e: AppHelper.callLater(0.4, capturer))


class Delegue(AppKit.NSObject):
    def webView_didFinishNavigation_(self, v, nav):
        AppHelper.callLater(attente, preparer)


delegue = Delegue.alloc().init()
vue.setNavigationDelegate_(delegue)
vue.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(url)))
AppHelper.callLater(30, AppHelper.stopEventLoop)      # garde-fou
AppHelper.runEventLoop()
