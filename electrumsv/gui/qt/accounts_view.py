import csv
from functools import partial
import json
from typing import Optional
import os
import threading
import time

from PyQt5.QtCore import pyqtSignal, QSize, Qt
from PyQt5.QtWidgets import (QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from electrumsv.bitcoin import address_from_string, script_template_to_string
from electrumsv.constants import DerivationType
from electrumsv.i18n import _
from electrumsv.wallet import AbstractAccount, MultisigAccount, Wallet

from .cosigners_view import CosignerState, CosignerList
from .main_window import ElectrumWindow
from .qrtextedit import ShowQRTextEdit
from .util import (Buttons, CancelButton, CloseButton, filename_field,
    FormSectionWidget, MessageBox, OkButton, protected, read_QIcon, WindowModalDialog)


class AccountsView(QSplitter):
    computing_privkeys_signal = pyqtSignal()
    show_privkeys_signal = pyqtSignal()

    def __init__(self, main_window: ElectrumWindow, wallet: Wallet) -> None:
        super().__init__(main_window)

        self._main_window = main_window
        self._wallet = wallet

        self._main_window.account_created_signal.connect(self._on_account_created)

        self._selection_list = QListWidget()
        self._selection_list.setMinimumWidth(150)
        self._tab_widget = QTabWidget()

        self.addWidget(self._selection_list)
        self.addWidget(self._tab_widget)

        self.setStretchFactor(1, 2)

        self._selection_list.setIconSize(QSize(32, 32))
        self._selection_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._selection_list.customContextMenuRequested.connect(self._create_account_menu)

        self._update_account_list()

    def _on_account_created(self, new_account_id: int) -> None:
        self._update_account_list()

    def get_tab_widget(self) -> QTabWidget:
        return self._tab_widget

    def _update_account_list(self) -> None:
        self._selection_list.clear()
        for account in self._wallet.get_accounts():
            item = QListWidgetItem()
            keystore = account.get_keystore()
            derivation_type = keystore.derivation_type if keystore is not None \
                else DerivationType.NONE
            is_watching_only = keystore.is_watching_only() if keystore is not None else True
            icon_state = "inactive" if is_watching_only else "active"
            if derivation_type == DerivationType.ELECTRUM_MULTISIG:
                tooltip_text = _("Multi-signature account")
                icon_filename = "icons8-group-task-80-blueui-{}.png"
            elif derivation_type == DerivationType.HARDWARE:
                tooltip_text = _("Hardware wallet account")
                icon_filename = "icons8-usb-2-80-blueui-{}.png"
            elif derivation_type == DerivationType.IMPORTED:
                # This should not be watch only as imported public keys have no keystore.
                tooltip_text = _("Imported private key account")
                icon_filename = "icons8-key-80-plus-blueui-{}.png"
            elif derivation_type == DerivationType.ELECTRUM_OLD:
                tooltip_text = _("Old-style Electrum account")
                icon_filename = "icons8-password-1-80-blueui-{}.png"
            elif derivation_type == DerivationType.BIP32:
                tooltip_text = _("BIP32 account")
                icon_filename ="icons8-grand-master-key-80-blueui-{}.png"
            else:
                # This should always be watch only as imported public keys have no keystore.
                tooltip_text = _("Imported public key account")
                icon_filename = "icons8-key-80-plus-blueui-{}.png"
            if is_watching_only:
                tooltip_text += f" ({_('watch only')})"
            item.setIcon(read_QIcon(icon_filename.format(icon_state)))
            item.setData(Qt.UserRole, account.get_id())
            item.setText(account.display_name())
            item.setToolTip(tooltip_text)
            self._selection_list.addItem(item)

    def _create_account_menu(self, position) -> None:
        item = self._selection_list.currentItem()
        if not item:
            return

        account_id = item.data(Qt.UserRole)
        account = self._wallet.get_account(account_id)

        menu = QMenu()
        menu.addAction(_("&Information"),
            partial(self._show_account_information, account_id))
        seed_menu = menu.addAction(_("View &Secured Data"),
            partial(self._view_secured_data, main_window=self._main_window, account_id=account_id))
        seed_menu.setEnabled(
            not account.is_watching_only() and not isinstance(account, MultisigAccount) \
            and not account.is_hardware_wallet())
        menu.addSeparator()

        private_keys_menu = menu.addMenu(_("&Private keys"))
        import_menu = private_keys_menu.addAction(_("&Import"), partial(self._import_privkey,
                main_window=self._main_window, account_id=account_id))
        import_menu.setEnabled(account.can_import_privkey())
        export_menu = private_keys_menu.addAction(_("&Export"), partial(self._export_privkeys,
            main_window=self._main_window, account_id=account_id))
        export_menu.setEnabled(account.can_export())
        if account.can_import_address():
            menu.addAction(_("Import addresses"), partial(self._import_addresses, account_id))

        menu.addSeparator()

        labels_menu = menu.addMenu(_("&Labels"))
        action = labels_menu.addAction(_("&Import"),
            partial(self._main_window.do_import_labels, account_id))
        labels_menu.addAction(_("&Export"), partial(self._main_window.do_export_labels, account_id))

        invoices_menu = menu.addMenu(_("Invoices"))
        invoices_menu.addAction(_("Import"),
            partial(self._main_window._import_invoices, account_id))

        menu.exec_(self._selection_list.viewport().mapToGlobal(position))

    def _show_account_information(self, account_id: int) -> None:
        dialog = AccountInformationDialog(self._main_window, self._wallet, account_id, self)
        dialog.exec_()

    def can_view_secured_data(self, account: AbstractAccount) -> None:
        return not account.is_watching_only() and not isinstance(account, MultisigAccount) \
            and not account.is_hardware_wallet()

    @protected
    def _view_secured_data(self, main_window: ElectrumWindow, account_id: int=-1,
            password: Optional[str]=None) -> None:
        # account_id is a keyword argument so that 'protected' can identity the correct wallet
        # window to do the password request in the context of.
        account = self._wallet.get_account(account_id)
        if self.can_view_secured_data(account):
            keystore = account.get_keystore()
            from .secured_data_dialog import SecuredDataDialog
            d = SecuredDataDialog(self._main_window, self, keystore, password)
            d.exec_()
        else:
            MessageBox.show_message(_("This type of account has no secured data. You are advised "
                "to manually back up this wallet."), self._main_window)

    @protected
    def _import_privkey(self, main_window: ElectrumWindow, account_id: int=-1,
            password: Optional[str]=None) -> None:
        # account_id is a keyword argument so that 'protected' can identity the correct wallet
        # window to do the password request in the context of.
        account = self._wallet.get_account(account_id)

        title, msg = _('Import private keys'), _("Enter private keys")
        self._main_window._do_import(title, msg,
            lambda x: account.import_private_key(x, password))

    def _import_addresses(self, account_id: int) -> None:
        account = self._wallet.get_account(account_id)

        title, msg = _('Import addresses'), _("Enter addresses")
        def import_addr(addr):
            address = address_from_string(addr)
            if account.import_address(address):
                return addr
            # Show duplicate addition same as good addition.
            return addr
        self._main_window._do_import(title, msg, import_addr)

    @protected
    def _export_privkeys(self, main_window: ElectrumWindow, account_id: int=-1,
            password: Optional[str]=None) -> None:
        account = self._wallet.get_account(account_id)

        if isinstance(self._wallet, MultisigAccount):
            MessageBox.show_message(
                _('WARNING: This is a multi-signature wallet.') + '\n' +
                _('It can not be "backed up" by simply exporting these private keys.')
            )

        d = WindowModalDialog(self, _('Private keys'))
        d.setMinimumSize(850, 300)
        vbox = QVBoxLayout(d)

        msg = "\n".join([
            _("WARNING: ALL your private keys are secret."),
            _("Exposing a single private key can compromise your entire wallet!"),
            _("In particular, DO NOT use 'redeem private key' services proposed by third parties.")
        ])
        vbox.addWidget(QLabel(msg))

        e = QTextEdit()
        e.setReadOnly(True)
        vbox.addWidget(e)

        defaultname = 'electrumsv-private-keys.csv'
        select_msg = _('Select file to export your private keys to')
        hbox, filename_e, csv_button = filename_field(self._main_window.config, defaultname,
            select_msg)
        vbox.addLayout(hbox)

        b = OkButton(d, _('Export'))
        b.setEnabled(False)
        vbox.addLayout(Buttons(CancelButton(d), b))

        private_keys = {}
        keyinstance_ids = account.get_keyinstance_ids()
        done = False
        cancelled = False
        def privkeys_thread():
            for keyinstance_id in keyinstance_ids:
                time.sleep(0.1)
                if done or cancelled:
                    break
                privkey = account.export_private_key(keyinstance_id, password)
                script_template = account.get_script_template_for_id(keyinstance_id)
                script_text = script_template_to_string(script_template)
                private_keys[script_text] = privkey
                self.computing_privkeys_signal.emit()
            if not cancelled:
                self.computing_privkeys_signal.disconnect()
                self.show_privkeys_signal.emit()

        def show_privkeys():
            s = "\n".join('{}\t{}'.format(script_text, privkey)
                          for script_text, privkey in private_keys.items())
            e.setText(s)
            b.setEnabled(True)
            self.show_privkeys_signal.disconnect()
            nonlocal done
            done = True

        def on_dialog_closed(*args):
            nonlocal done
            nonlocal cancelled
            if not done:
                cancelled = True
                self.computing_privkeys_signal.disconnect()
                self.show_privkeys_signal.disconnect()

        self.computing_privkeys_signal.connect(lambda: e.setText(
            "Please wait... %d/%d" % (len(private_keys),len(keyinstance_ids))))
        self.show_privkeys_signal.connect(show_privkeys)
        d.finished.connect(on_dialog_closed)
        threading.Thread(target=privkeys_thread).start()

        if not d.exec_():
            done = True
            return

        filename = filename_e.text()
        if not filename:
            return

        try:
            self.do_export_privkeys(filename, private_keys, csv_button.isChecked())
        except (IOError, os.error) as reason:
            txt = "\n".join([
                _("ElectrumSV was unable to produce a private key-export."),
                str(reason)
            ])
            MessageBox.show_error(txt, title=_("Unable to create csv"))
        except Exception as e:
            MessageBox.show_message(str(e), self._main_window)
            return

        MessageBox.show_message(_('Private keys exported'), self._main_window)

    def do_export_privkeys(self, fileName: str, pklist, is_csv):
        with open(fileName, "w+") as f:
            if is_csv:
                transaction = csv.writer(f)
                transaction.writerow(["reference", "private_key"])
                for key_text, pk in pklist.items():
                    transaction.writerow([key_text, pk])
            else:
                f.write(json.dumps(pklist, indent = 4))



class AccountInformationDialog(QDialog):
    _list: Optional[CosignerList] = None

    def __init__(self, main_window: ElectrumWindow, wallet: Wallet, account_id: int,
            parent: QWidget) -> None:
        super().__init__(parent)

        self._main_window = main_window
        self._wallet = wallet

        self._account = account = self._wallet.get_account(account_id)
        keystore = account.get_keystore()

        self.setWindowTitle(_("Account Information"))
        self.setMinimumSize(600, 400)

        vbox = QVBoxLayout()

        self._form = form = FormSectionWidget(minimum_label_width=160)
        form.add_title("Account properties")
        name_widget = QLineEdit()
        name_widget.setText(account.display_name())
        name_widget.setReadOnly(True)
        form.add_row(_("Name"), name_widget, True)
        form.add_row(_("Type"), QLabel(account.type().value))
        script_type_widget = QLineEdit()
        script_type_widget.setText(account.get_default_script_type().name)
        script_type_widget.setReadOnly(True)
        form.add_row(_("Script type"), script_type_widget, True)
        vbox.addWidget(form)

        add_stretch = True
        if keystore is not None:
            if keystore.derivation_type == DerivationType.ELECTRUM_MULTISIG:
                multisig_form = FormSectionWidget(minimum_label_width=160)
                multisig_form.add_title("Multi-signature properties")
                multisig_form.add_row(_("Number of cosigners"), QLabel(str(keystore.n)))
                multisig_form.add_row(_("Number of signatures required"), QLabel(str(keystore.m)))
                vbox.addWidget(multisig_form)

                self._list = list = CosignerList(self._main_window, create=False)
                list.setMinimumHeight(350)
                for i, keystore in enumerate(account.get_keystores()):
                    state = CosignerState(i, keystore)
                    list.add_state(state)
                vbox.addWidget(list, 1)
                add_stretch = False
            elif account.is_deterministic():
                form.add_row(_("Keystore"), QLabel(keystore.type().value))

                mpk_list = account.get_master_public_keys()
                mpk_text = ShowQRTextEdit()
                mpk_text.setFixedHeight(65)
                mpk_text.addCopyButton(self._main_window.app)
                mpk_text.setText(mpk_list[0])
                mpk_text.repaint()   # macOS hack for Electrum #4777
                form.add_row(QLabel(_("Master public key")), mpk_text, True)
        if add_stretch:
            vbox.addStretch(1)

        buttons = Buttons(CloseButton(self))
        self._buttons = buttons

        vbox.addLayout(self._buttons)
        self.setLayout(vbox)
