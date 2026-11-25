# -*- coding: utf-8 -*-
# Movie Browser - online updater (zip from GitHub, all files loose in the zip)
from __future__ import print_function
from os import makedirs, remove
from os.path import exists, join, dirname, abspath
from re import search, findall, M
from io import BytesIO
from threading import Thread
import ssl
import zipfile

try:
    from urllib.request import urlopen, Request
except ImportError:
    from urllib2 import urlopen, Request

from twisted.internet import reactor
from enigma import quitMainloop
from Tools import Notifications
from Screens.MessageBox import MessageBox
from Screens.Standby import TryQuitMainloop
from . import _

PLUGIN_DIR = "/usr/lib/enigma2/python/Plugins/Extensions/MovieBrowser"

# >>> aanpassen naar je eigen GitHub-repo <<<
PLUGIN_ZIP_URL = "https://github.com/Caught/MovieBrowser/archive/refs/heads/main.zip"
REMOTE_PLUGIN_URL = "https://raw.githubusercontent.com/Caught/MovieBrowser/refs/heads/main/plugin.py"

# deze mappen blijven bij een update onaangeroerd (database, cache, logs)
KEEP = ("db/", "log/")


def _open(url, timeout=20):
    req = Request(url, headers={"User-Agent": "MovieBrowser-Updater"})
    try:
        return urlopen(req, timeout=timeout)
    except Exception:
        # oudere images (python 2 / oude CA-lijst): zonder certificaatcontrole
        ctx = ssl._create_unverified_context()
        return urlopen(req, timeout=timeout, context=ctx)


def _vt(v):
    return tuple(int(p) for p in findall(r"\d+", str(v)))


def getRemoteVersion():
    data = _open(REMOTE_PLUGIN_URL).read().decode("utf-8", "ignore")
    m = search(r"^version\s*=\s*['\"]([^'\"]+)['\"]", data, M)
    if not m:
        raise Exception("version not found in remote plugin.py")
    return m.group(1)


def installZip(data):
    z = zipfile.ZipFile(BytesIO(data))
    if z.testzip() is not None:
        raise Exception("corrupt zip")
    names = [n for n in z.namelist() if not n.endswith("/")]
    prefix = ""
    if "plugin.py" not in names:
        # GitHub-archive heeft een bovenliggende map: die weglaten
        top = names[0].split("/")[0] + "/" if names else ""
        if top and all(n.startswith(top) for n in names) \
                and top + "plugin.py" in names:
            prefix = top
        else:
            raise Exception("plugin.py not found in zip")
    base = abspath(PLUGIN_DIR)
    # eerst alles in het geheugen lezen, dan pas schrijven
    files = [(n[len(prefix):], z.read(n)) for n in names]
    count = 0
    for rel, content in files:
        if not rel or rel.startswith(KEEP):
            continue
        target = abspath(join(base, rel))
        if not target.startswith(base + "/"):
            continue
        folder = dirname(target)
        if not exists(folder):
            makedirs(folder)
        with open(target, "wb") as f:
            f.write(content)
        if target.endswith(".py") and exists(target + "c"):
            remove(target + "c")
        count += 1
    return count


class PluginUpdater(object):

    # mode: 'ask'     = via menu (met schermen, ook "al up-to-date"/fouten)
    #       'open'    = bij openen van de plugin: stil als er niets nieuws is,
    #                   anders vraag: downloaden? ja/nee -> herstart? ja/nee
    #       'check'   = stil controleren en melden
    #       'install' = stil controleren en installeren
    def __init__(self, session, current_version, mode='ask'):
        self.session = session
        self.current = current_version
        self.mode = mode
        self.remote = None

    def start(self):
        Thread(target=self._check).start()

    # ---- versiecontrole (achtergrond-thread) ----
    def _check(self):
        remote, err = None, None
        try:
            remote = getRemoteVersion()
        except Exception as e:
            err = str(e)
        reactor.callFromThread(self._checked, remote, err)

    def _checked(self, remote, err):
        silent = self.mode != 'ask'
        if err:
            if not silent:
                self.session.open(
                    MessageBox, _("Update check failed:\n%s") % err,
                    MessageBox.TYPE_ERROR, timeout=10)
            return
        self.remote = remote
        if _vt(remote) <= _vt(self.current):
            if not silent:
                self.session.open(
                    MessageBox,
                    _("You already have the latest version (%s).") %
                    self.current, MessageBox.TYPE_INFO, timeout=5)
            return
        if self.mode == 'install':
            Thread(target=self._download).start()
            return
        if self.mode == 'check':
            Notifications.AddPopup(
                _("Movie Browser: new version %s available (installed: %s).\n"
                  "Install it via the plugin setup.") % (remote, self.current),
                MessageBox.TYPE_INFO, 15)
            return
        self._ask(
            self._answer,
            _("New version %s available (installed: %s).\nInstall now?") % (
                remote, self.current))

    # vraag tonen: bovenop het huidige scherm, anders als melding
    def _ask(self, callback, text):
        try:
            self.session.openWithCallback(
                callback, MessageBox, text, MessageBox.TYPE_YESNO)
        except Exception:
            Notifications.AddNotificationWithCallback(
                callback, MessageBox, text, MessageBox.TYPE_YESNO)

    def _info(self, text, kind, timeout):
        try:
            self.session.open(MessageBox, text, kind, timeout=timeout)
        except Exception:
            Notifications.AddPopup(text, kind, timeout)

    # ---- downloaden + installeren (achtergrond-thread) ----
    def _answer(self, answer):
        if answer:
            Thread(target=self._download).start()

    def _download(self):
        count, err = 0, None
        try:
            count = installZip(_open(PLUGIN_ZIP_URL, 60).read())
        except Exception as e:
            err = str(e)
        reactor.callFromThread(self._installed, count, err)

    def _installed(self, count, err):
        if self.mode in ('check', 'install'):
            if not err:
                Notifications.AddNotificationWithCallback(
                    self._restartSilent, MessageBox,
                    _("Movie Browser updated to version %s.\n"
                      "Restart GUI now?") % self.remote,
                    MessageBox.TYPE_YESNO, 30)
            return
        if err:
            self._info(_("Update failed:\n%s") % err,
                       MessageBox.TYPE_ERROR, 10)
            return
        self._ask(
            self._restartSilent if self.mode == 'open' else self._restart,
            _("Updated to version %s (%d files).\nRestart GUI now?") % (
                self.remote, count))

    def _restart(self, answer):
        if answer:
            self.session.open(TryQuitMainloop, 3)

    def _restartSilent(self, answer):
        if answer:
            quitMainloop(3)


def autoCheck(session, current_version, mode):
    # 90 s wachten, zodat het netwerk na het opstarten klaar is
    if mode in ('check', 'install'):
        updater = PluginUpdater(session, current_version, mode)
        reactor.callLater(90, updater.start)
