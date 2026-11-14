"""Tests de l'éditeur dans WebKit : charge une page, exécute un script, imprime window.__log au fil de l'eau.

Usage : python sonde.py <url> <script.js> <durée s>
"""
import json
import sys

import AppKit
import Foundation
import WebKit
from PyObjCTools import AppHelper

url, script, duree = sys.argv[1], open(sys.argv[2]).read(), float(sys.argv[3])
app = AppKit.NSApplication.sharedApplication()
app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
fen = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((-10000, -10000), (1200, 800)), AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
conf = WebKit.WKWebViewConfiguration.alloc().init()
conf.setMediaTypesRequiringUserActionForPlayback_(0)
vue = WebKit.WKWebView.alloc().initWithFrame_configuration_(((0, 0), (1200, 800)), conf)
fen.setContentView_(vue)
fen.orderFront_(None)
lus = [0]


def lire():
    def h(r, e):
        if r:
            l = json.loads(r)
            for x in l[lus[0]:]:
                print(x, flush=True)
            lus[0] = len(l)
    vue.evaluateJavaScript_completionHandler_('JSON.stringify(window.__log||[])', h)
    AppHelper.callLater(1, lire)


class D(AppKit.NSObject):
    def webView_didFinishNavigation_(self, v, n):
        AppHelper.callLater(2, lambda: vue.evaluateJavaScript_completionHandler_(script, lambda r, e: print('script', e) if e else None))
        AppHelper.callLater(3, lire)


d = D.alloc().init()
vue.setNavigationDelegate_(d)
vue.loadRequest_(Foundation.NSURLRequest.requestWithURL_(Foundation.NSURL.URLWithString_(url)))
AppHelper.callLater(duree, AppHelper.stopEventLoop)
AppHelper.runEventLoop()
