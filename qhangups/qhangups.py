#!/usr/bin/env python

import sys, os, logging, argparse, asyncio, signal
from PyQt4 import QtCore, QtGui

import appdirs
import hangups
from hangups.ui.notify import Notifier
from quamash import QEventLoop

from qhangups.version import __version__
from qhangups.settings import QHangupsSettings
from qhangups.conversations import QHangupsConversations
from qhangups.conversationslist import QHangupsConversationsList


# Basic settings
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

# Prepare Qt translators
translator = QtCore.QTranslator()
qt_translator = QtCore.QTranslator()


class QHangupsMainWidget(QtGui.QWidget):
    """QHangups main widget (icon in system tray)"""
    def __init__(self, cookies_path, parent=None):
        super().__init__(parent)
        self.set_language()

        self.cookies_path = cookies_path
        self.hangups_running = False
        self.client = None

        self.create_actions()
        self.create_menu()
        self.create_icon()
        self.update_status()

        # These are populated by on_connect when it's called.
        self.conv_list = None  # hangups.ConversationList
        self.user_list = None  # hangups.UserList
        self.notifier = None   # hangups.notify.Notifier

        # Widgets
        self.conversations_dialog = None
        self.messages_dialog = None

        # Setup system tray icon doubleclick timer
        self.icon_doubleclick_timer = QtCore.QTimer(self)
        self.icon_doubleclick_timer.setSingleShot(True)
        self.icon_doubleclick_timer.timeout.connect(self.icon_doubleclick_timeout)

        # Handle signals on Unix
        # (add_signal_handler is not implemented on Windows)
        try:
            loop = asyncio.get_event_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(signum, lambda: self.quit(force=True))
        except NotImplementedError:
            pass

    def create_actions(self):
        """Create actions and connect relevant signals"""
        self.startAction = QtGui.QAction(self)
        self.startAction.triggered.connect(self.hangups_start)
        self.stopAction = QtGui.QAction(self)
        self.stopAction.triggered.connect(self.hangups_stop)
        self.settingsAction = QtGui.QAction(self)
        self.settingsAction.triggered.connect(self.settings)
        self.aboutAction = QtGui.QAction(self)
        self.aboutAction.triggered.connect(self.about)
        self.quitAction = QtGui.QAction(self)
        self.quitAction.triggered.connect(self.quit)

    def create_menu(self):
        """Create menu and add items to it"""
        self.trayIconMenu = QtGui.QMenu(self)
        self.trayIconMenu.addAction(self.startAction)
        self.trayIconMenu.addAction(self.stopAction)
        self.trayIconMenu.addSeparator()
        self.trayIconMenu.addAction(self.settingsAction)
        self.trayIconMenu.addAction(self.aboutAction)
        self.trayIconMenu.addSeparator()
        self.trayIconMenu.addAction(self.quitAction)

    def create_icon(self):
        """Create system tray icon"""
        self.trayIcon = QtGui.QSystemTrayIcon(self)
        self.iconActive = QtGui.QIcon("{}/qhangups.svg".format(os.path.dirname(os.path.abspath(__file__))))
        self.iconDisabled = QtGui.QIcon("{}/qhangups_disabled.svg".format(os.path.dirname(os.path.abspath(__file__))))
        self.trayIcon.activated.connect(self.icon_activated)
        self.trayIcon.setContextMenu(self.trayIconMenu)
        self.trayIcon.setIcon(self.iconDisabled)
        self.trayIcon.setToolTip("QHangups")
        self.trayIcon.show()

    def retranslateUi(self):
        """Retranslate GUI"""
        self.startAction.setText(self.tr("&Connect"))
        self.stopAction.setText(self.tr("&Disconnect"))
        self.settingsAction.setText(self.tr("S&ettings ..."))
        self.aboutAction.setText(self.tr("A&bout ..."))
        self.quitAction.setText(self.tr("&Quit"))

    def login(self, cookies_path):
        """Login to Google account"""
        try:
            cookies = hangups.auth.get_auth(self.get_credentials, self.get_pin, cookies_path)
            return cookies
        except hangups.GoogleAuthError:
            QtGui.QMessageBox.warning(self, self.tr("QHangups - Warning"),
                                      self.tr("Google login failed!"))
            return False

    def get_credentials(self):
        """Ask user for email and password (callback)"""
        email, ok = QtGui.QInputDialog.getText(self, self.tr("QHangups - Email"),
                                               self.tr("Email:"),
                                               QtGui.QLineEdit.Normal)
        if ok:
            password, ok = QtGui.QInputDialog.getText(self, self.tr("QHangups - Password"),
                                                      self.tr(u"Password:"),
                                                      QtGui.QLineEdit.Password)
            if ok:
                return (email, password)
            else:
                return False
        else:
            return False

    def get_pin(self):
        """Ask user for second factor PIN (callback)"""
        pin, ok = QtGui.QInputDialog.getText(self, self.tr("QHangups - PIN"),
                                             self.tr("PIN:"),
                                             QtGui.QLineEdit.Password)
        if ok:
            return pin
        else:
            return False

    def update_status(self):
        """Update GUI according to Hangups status"""
        if self.hangups_running:
            self.trayIcon.setIcon(self.iconActive)
            self.startAction.setEnabled(False)
            self.stopAction.setEnabled(True)
        else:
            self.trayIcon.setIcon(self.iconDisabled)
            self.startAction.setEnabled(True)
            self.stopAction.setEnabled(False)

    def hangups_start(self):
        """Connect to Hangouts"""
        cookies = self.login(self.cookies_path)
        if cookies:
            self.client = hangups.Client(cookies)
            self.client.on_connect.add_observer(self.on_connect)

            # Run Hangups event loop
            asyncio.async(
                self.client.connect()
            ).add_done_callback(lambda future: future.result())
            self.hangups_running = True
            self.update_status()

    def hangups_stop(self):
        """Disconnect from Hangouts"""
        asyncio.async(
            self.client.disconnect()
        ).add_done_callback(lambda future: future.result())

        self.conv_list = None
        self.user_list = None
        self.notifier = None

        self.conversations_dialog = None
        self.messages_dialog = None

        self.hangups_running = False
        self.client = None
        self.update_status()

    def about(self):
        """Show About dialog"""
        QtGui.QMessageBox.information(self, self.tr("About"), self.tr("QHangups {}".format(__version__)))

    def settings(self):
        """Show Settings dialog"""
        dialog = QHangupsSettings(self)
        if dialog.exec_():
            self.set_language()
            if self.hangups_running:
                self.hangups_stop()
                self.hangups_start()

    def set_language(self):
        """Change language"""
        settings = QtCore.QSettings()

        language = settings.value("language")
        if not language:
            language = QtCore.QLocale.system().name().split("_")[0]

        lang_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "languages")
        lang_file = "qhangups_{}.qm".format(language)

        qt_lang_path = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
        qt_lang_file = "qt_{}.qm".format(language)

        if os.path.isfile(os.path.join(lang_path, lang_file)):
            translator.load(lang_file, lang_path)
            qt_translator.load(qt_lang_file, qt_lang_path)
        else:
            translator.load("")
            qt_translator.load("")

    def icon_activated(self, reason):
        """Connect or disconnect from Hangouts by double-click on tray icon"""
        if reason == QtGui.QSystemTrayIcon.Trigger:
            if self.icon_doubleclick_timer.isActive():
                self.icon_doubleclick_timer.stop()
                if self.hangups_running:
                    self.hangups_stop()
                else:
                    self.hangups_start()
            else:
                self.icon_doubleclick_timer.start(QtGui.qApp.doubleClickInterval())

    def icon_doubleclick_timeout(self):
        """Open or close list of conversations after single-click on tray icon"""
        if self.conversations_dialog and self.conversations_dialog.isVisible():
            self.conversations_dialog.hide()
        elif self.conversations_dialog:
            self.conversations_dialog.show()

    def quit(self, force=False):
        """Quit QHangups"""
        if self.hangups_running:
            if not force:
                reply = QtGui.QMessageBox.question(self, self.tr("QHangups - Quit"),
                                                   self.tr("You are still connected to Google Hangouts. "
                                                           "Do you really want to quit QHangups?"),
                                                   QtGui.QMessageBox.Yes | QtGui.QMessageBox.No, QtGui.QMessageBox.No)
                if reply != QtGui.QMessageBox.Yes:
                    return
            self.hangups_stop()

        loop = asyncio.get_event_loop()
        loop.stop()
        # QtGui.qApp.quit()

    def changeEvent(self, event):
        """Handle LanguageChange event"""
        if (event.type() == QtCore.QEvent.LanguageChange):
            print("Language changed")
            self.retranslateUi()

        QtGui.QWidget.changeEvent(self, event)

    def open_messages_dialog(self, conv_id, switch=True):
        """Open conversation in new tab"""
        if not self.messages_dialog:
            self.messages_dialog = QHangupsConversations(self.client, self.conv_list, self)
        self.messages_dialog.set_conv_tab(conv_id, switch=switch)
        self.messages_dialog.show()

    def on_connect(self, initial_data):
        """Handle connecting for the first time (callback)"""
        print('Connected')
        self.user_list = hangups.UserList(self.client,
                                          initial_data.self_entity,
                                          initial_data.entities,
                                          initial_data.conversation_participants)
        self.conv_list = hangups.ConversationList(self.client,
                                                  initial_data.conversation_states,
                                                  self.user_list,
                                                  initial_data.sync_timestamp)
        self.conv_list.on_event.add_observer(self.on_event)

        self.notifier = Notifier(self.conv_list)

        self.conversations_dialog = QHangupsConversationsList(self.client, self.conv_list, self)
        self.conversations_dialog.show()

    def on_event(self, conv_event):
        """Open conversation tab for new messages when they arrive (callback)"""
        if isinstance(conv_event, hangups.ChatMessageEvent):
            if not self.messages_dialog:
                self.messages_dialog = QHangupsConversations(self.client, self.conv_list, self)
            self.messages_dialog.set_conv_tab(conv_event.conversation_id)
            self.messages_dialog.show()


def main():
    # Build default paths for files.
    dirs = appdirs.AppDirs('QHangups', 'QHangups')
    default_log_path = os.path.join(dirs.user_data_dir, 'hangups.log')
    default_cookies_path = os.path.join(dirs.user_data_dir, 'cookies.json')

    # Setup command line argument parser
    parser = argparse.ArgumentParser(prog='qhangups',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('-d', '--debug', action='store_true',
                        help='log detailed debugging messages')
    parser.add_argument('--log', default=default_log_path,
                        help='log file path')
    parser.add_argument('--cookies', default=default_cookies_path,
                        help='cookie storage path')
    args = parser.parse_args()

    # Create all necessary directories.
    for path in [args.log, args.cookies]:
        directory = os.path.dirname(path)
        if directory and not os.path.isdir(directory):
            try:
                os.makedirs(directory)
            except OSError as e:
                sys.exit('Failed to create directory: {}'.format(e))

    # Setup logging
    log_level = logging.DEBUG if args.debug else logging.WARNING
    logging.basicConfig(filename=args.log, level=log_level, format=LOG_FORMAT)
    # asyncio's debugging logs are VERY noisy, so adjust the log level
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    # ...and if we don't need Hangups debug logs, then uncomment this:
    # logging.getLogger('hangups').setLevel(logging.WARNING)

    # Setup QApplication
    app = QtGui.QApplication(sys.argv)
    app.setOrganizationName("QHangups")
    app.setOrganizationDomain("qhangups.eutopia.cz")
    app.setApplicationName("QHangups")
    app.setQuitOnLastWindowClosed(False)
    app.installTranslator(translator)
    app.installTranslator(qt_translator)

    # Start Quamash event loop
    loop = QEventLoop(app)
    with loop:
        widget = QHangupsMainWidget(args.cookies)
        loop.run_forever()


if __name__ == "__main__":
    main()
