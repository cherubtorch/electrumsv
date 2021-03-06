import bitcoinx
import os
import pytest
import sqlite3
import tempfile
from typing import List

from electrumsv.constants import (TxFlags, ScriptType, DerivationType, TransactionOutputFlag,
    PaymentState, WalletEventFlag, WalletEventType)
from electrumsv.logs import logs
from electrumsv.wallet_database import (AccountTable, DatabaseContext, KeyInstanceTable,
    MasterKeyTable, migration, PaymentRequestTable, SynchronousWriter, TransactionTable,
    TransactionDeltaTable, TransactionOutputTable, TxData, TxProof)
from electrumsv.wallet_database.tables import (AccountRow, KeyInstanceRow,
    MAGIC_UNTOUCHED_BYTEDATA, MasterKeyRow, PaymentRequestRow, TransactionDeltaRow,
    WalletEventRow, WalletEventTable)

logs.set_level("debug")


tx_hex_1 = ("01000000011a284a701e6a69ba68ac4b1a4509ac04f5c10547e3165fe869d5e910fe91bc4c04000000"
    "6b483045022100e81ce3382de4d63efad1e2bc4a7ebe70fb03d8451c1bc176b2dfd310f7a636f302200eab4382"
    "9f9d4c94be41c640f9f6261657dcac6dc345718b89e7a80645dbe27f412102defddf740fa60b0dcdc88578d9de"
    "a51350db9245e4f1a5072be00e9fb0573fddffffffff02a0860100000000001976a914717b9a7840ef60ef2e2a"
    "6fca85d55988e070137988acda837e18000000001976a914c0eab5430fd02e18edfc28607eae975001e7560488"
    "ac00000000")

tx_hex_2 = ("010000000113529b6e34ceebfa3911c569b568ef48b95cc25d4c5c6a5b2435d30c9dbcc8af0000000"
    "06b483045022100876dfdc3228ff561531c3ba02e2ad9628230f02ef5036599e1c95b747e1731ac02205ed9ff1"
    "14adc6e7ca58b889272afa695d7f62902bb81286bb46aee7d3a31201e412102642f0cfdb3065d34276c8af2183"
    "e7d0d8e8e2ce85723eb6fe4942d0db949a225ffffffff027c150000000000001976a91439826f4659bba2a224b"
    "87b1812206fd4efc9ada388acc0dd3e00000000001976a914337106761eb441a326d4027f6d5aa19eed550c298"
    "8ac00000000")


def _db_context():
    wallet_path = os.path.join(tempfile.mkdtemp(), "wallet_create")
    assert not os.path.exists(wallet_path)
    migration.create_database_file(wallet_path)
    return DatabaseContext(wallet_path)

@pytest.fixture
def db_context():
    return _db_context()


def test_migrations() -> None:
    # Do all the migrations apply cleanly?
    wallet_path = os.path.join(tempfile.mkdtemp(), "wallet_create")
    migration.create_database_file(wallet_path)


@pytest.mark.timeout(8)
def test_table_masterkeys_crud(db_context: DatabaseContext) -> None:
    table = MasterKeyTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    line1 = MasterKeyRow(1, None, 2, b'111')
    line2 = MasterKeyRow(2, None, 4, b'222')

    with SynchronousWriter() as writer:
        table.create([ line1 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.create([ line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    lines = table.read()
    assert 2 == len(lines)

    line1_db = [ line for line in lines if line[0] == 1 ][0]
    assert line1 == line1_db

    line2_db = [ line for line in lines if line[0] == 2 ][0]
    assert line2 == line2_db

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update_derivation_data([ (b'234', 1) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.delete([ 2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    lines = table.read()
    assert 1 == len(lines)
    assert lines[0].masterkey_id == 1
    assert lines[0].derivation_data == b'234'


@pytest.mark.timeout(8)
def test_table_accounts_crud(db_context: DatabaseContext) -> None:
    table = AccountTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    ACCOUNT_ID = 10
    MASTERKEY_ID = 20

    line1 = AccountRow(ACCOUNT_ID+1, MASTERKEY_ID+1, ScriptType.P2PKH, 'name1')
    line2 = AccountRow(ACCOUNT_ID+2, MASTERKEY_ID+1, ScriptType.P2PK, 'name2')

    # No effect: The masterkey foreign key constraint will fail as the masterkey does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    mktable = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        mktable.create([ MasterKeyRow(MASTERKEY_ID+1, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the first row.
    with SynchronousWriter() as writer:
        table.create([ line1 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the second row.
    with SynchronousWriter() as writer:
        table.create([ line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line[0] == line1[0] ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line[0] == line2[0] ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update_masterkey([ (MASTERKEY_ID+1, ScriptType.MULTISIG_BARE, line1[0]) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.update_name([ ('new_name', line2[0]) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line[0] == line1[0] ][0]
    assert ScriptType.MULTISIG_BARE == db_line1[2]
    db_line2 = [ db_line for db_line in db_lines if db_line[0] == line2[0] ][0]
    assert 'new_name' == db_line2[3]

    with SynchronousWriter() as writer:
        table.delete([ line2[0] ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0][0] == line1[0]


@pytest.mark.timeout(8)
def test_table_keyinstances_crud(db_context: DatabaseContext) -> None:
    table = KeyInstanceTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    KEYINSTANCE_ID = 0
    ACCOUNT_ID = 10
    MASTERKEY_ID = 20
    DERIVATION_DATA1 = b'111'
    DERIVATION_DATA2 = b'222'
    SCRIPT_TYPE = 40

    line1 = KeyInstanceRow(KEYINSTANCE_ID+1, ACCOUNT_ID+1, MASTERKEY_ID+1, DerivationType.BIP32,
        DERIVATION_DATA1, SCRIPT_TYPE+1, True, None)
    line2 = KeyInstanceRow(KEYINSTANCE_ID+2, ACCOUNT_ID+1, MASTERKEY_ID+1, DerivationType.HARDWARE,
        DERIVATION_DATA2, SCRIPT_TYPE+2, True, None)

    # No effect: The masterkey foreign key constraint will fail as the masterkey does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    mktable = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        mktable.create([ MasterKeyRow(MASTERKEY_ID+1, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The account foreign key constraint will fail as the account does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the account foreign key constraint by creating the account.
    acctable = AccountTable(db_context)
    with SynchronousWriter() as writer:
        acctable.create([ AccountRow(ACCOUNT_ID+1, MASTERKEY_ID+1, ScriptType.P2PKH, 'name') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the first row.
    with SynchronousWriter() as writer:
        table.create([ line1 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the second row.
    with SynchronousWriter() as writer:
        table.create([ line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line[0] == line1[0] ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line[0] == line2[0] ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update_derivation_data([ (b'234', line1[0]) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.update_flags([ (False, line2[0]) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line[0] == line1[0] ][0]
    assert b'234' == db_line1[4]
    db_line2 = [ db_line for db_line in db_lines if db_line[0] == line2[0] ][0]
    assert not db_line2[6]

    with SynchronousWriter() as writer:
        table.delete([ line2[0] ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0].keyinstance_id == line1.keyinstance_id
    assert db_lines[0].description is None
    assert db_lines[0].derivation_data == b'234'

    # Now try out the labels.
    with SynchronousWriter() as writer:
        table.update_descriptions([ ("line1", line1.keyinstance_id) ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    rows = table.read()
    assert len(rows) == 1
    assert rows[0].keyinstance_id == line1[0]
    assert rows[0].description == "line1"


class TestTransactionTable:
    @classmethod
    def setup_class(cls):
        cls.db_context = _db_context()
        cls.store = TransactionTable(cls.db_context)

        cls.tx_hash = os.urandom(32)

    @classmethod
    def teardown_class(cls):
        cls.store.close()
        cls.db_context.close()

    def setup_method(self):
        db = self.store._db
        db.execute(f"DELETE FROM Transactions")
        db.commit()

    def _get_store_hashes(self) -> List[bytes]:
        return [ row[0] for row in self.store.read_metadata() ]

    def test_proof_serialization(self):
        proof1 = TxProof(position=10, branch=[ os.urandom(32) for i in range(10) ])
        raw = self.store._pack_proof(proof1)
        proof2 = self.store._unpack_proof(raw)
        assert proof1.position == proof2.position
        assert proof1.branch == proof2.branch

    @pytest.mark.timeout(8)
    def test_create1(self):
        bytedata_1 = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(bytedata_1)
        metadata_1 = TxData(height=None, fee=None, position=None, date_added=1, date_updated=1)
        with SynchronousWriter() as writer:
            self.store.create([ (tx_hash, metadata_1, bytedata_1, TxFlags.StateDispatched, None) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Check the state is correct, all states should be the same code path.
        _tx_hash, flags, _metadata = self.store.read_metadata(tx_hashes=[tx_hash])[0]
        assert TxFlags.StateDispatched == flags & TxFlags.STATE_MASK

        _tx_hash, bytedata_2, _flags, metadata_2 = self.store.read(tx_hashes=[tx_hash])[0]
        assert metadata_1 == metadata_2
        assert bytedata_1 == bytedata_2

    @pytest.mark.timeout(8)
    def test_create2(self) -> None:
        to_add = []
        for i in range(10):
            tx_bytes = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(tx_bytes)
            tx_data = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
            to_add.append((tx_hash, tx_data, tx_bytes, TxFlags.Unset, None))
        with SynchronousWriter() as writer:
            self.store.create(to_add, completion_callback=writer.get_callback())
            assert writer.succeeded()

        existing_tx_hashes = set(self._get_store_hashes())
        added_tx_hashes = set(t[0] for t in to_add)
        assert added_tx_hashes == existing_tx_hashes

    @pytest.mark.timeout(8)
    def test_update(self):
        to_add = []
        for i in range(10):
            tx_bytes = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(tx_bytes)
            tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
            if i % 2:
                to_add.append((tx_hash, tx_data, tx_bytes, TxFlags.HasByteData, None))
            else:
                to_add.append((tx_hash, tx_data, None, TxFlags.Unset, None))
        with SynchronousWriter() as writer:
            self.store.create(to_add, completion_callback=writer.get_callback())
            assert writer.succeeded()

        to_update = []
        for tx_hash, metadata, tx_bytes, flags, description in to_add:
            tx_metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
            to_update.append((tx_hash, tx_metadata, tx_bytes, flags))
        with SynchronousWriter() as writer:
            self.store.update(to_update, completion_callback=writer.get_callback())
            assert writer.succeeded()

        for get_tx_hash, bytedata_get, flags_get, metadata_get in self.store.read():
            for update_tx_hash, update_metadata, update_tx_bytes, update_flags in to_update:
                if update_tx_hash == get_tx_hash:
                    assert metadata_get == update_metadata
                    assert bytedata_get == update_tx_bytes
                    continue

    @pytest.mark.timeout(8)
    def test_update__entry_with_set_bytedata_flag(self):
        tx_bytes = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(tx_bytes)
        tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
        row = (tx_hash, tx_data, tx_bytes, TxFlags.HasByteData, None)
        with SynchronousWriter() as writer:
            self.store.create([ row ], completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Ensure that a set bytedata flag requires bytedata to be included.
        with pytest.raises(AssertionError):
            self.store.update([(tx_hash, tx_data, None, TxFlags.HasByteData)])

    @pytest.mark.timeout(8)
    def test_update__entry_with_unset_bytedata_flag(self):
        tx_bytes = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(tx_bytes)
        tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
        row = (tx_hash, tx_data, tx_bytes, TxFlags.HasByteData, None)
        with SynchronousWriter() as writer:
            self.store.create([ row ], completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Ensure that a unset bytedata flag requires bytedata to not be included.
        with pytest.raises(AssertionError):
            self.store.update([(tx_hash, tx_data, tx_bytes, TxFlags.Unset)])

    @pytest.mark.timeout(8)
    def test_update__entry_with_magic_bytedata_and_set_flag(self):
        tx_bytes = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(tx_bytes)
        tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
        row = (tx_hash, tx_data, tx_bytes, TxFlags.HasByteData, None)
        with SynchronousWriter() as writer:
            self.store.create([ row ], completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Ensure that the magic bytedata requires a set bytedata flag.
        with pytest.raises(AssertionError):
            self.store.update([(tx_hash, tx_data, MAGIC_UNTOUCHED_BYTEDATA, TxFlags.Unset)])

    @pytest.mark.timeout(8)
    def test_update__with_valid_magic_bytedata(self):
        tx_bytes = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(tx_bytes)
        tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
        row = (tx_hash, tx_data, tx_bytes, TxFlags.HasByteData, None)
        with SynchronousWriter() as writer:
            self.store.create([ row ], completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Ensure that
        with SynchronousWriter() as writer:
            self.store.update([(tx_hash, tx_data, MAGIC_UNTOUCHED_BYTEDATA, TxFlags.HasByteData)],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        rows = self.store.read()
        assert 1 == len(rows)
        get_tx_hash, bytedata_get, flags_get, metadata_get = rows[0]
        assert tx_bytes == bytedata_get
        assert flags_get & TxFlags.HasByteData != 0

    @pytest.mark.timeout(8)
    def test_update_flags(self):
        bytedata = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(bytedata)
        metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
        with SynchronousWriter() as writer:
            self.store.create([ (tx_hash, metadata, bytedata, TxFlags.Unset, None) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Verify the field flags are assigned correctly on the add.
        expected_flags = TxFlags.HasByteData | TxFlags.HasFee | TxFlags.HasHeight
        _tx_hash, flags, _metadata = self.store.read_metadata(tx_hashes=[tx_hash])[0]
        assert expected_flags == flags, f"expected {expected_flags!r}, got {TxFlags.to_repr(flags)}"

        flags = TxFlags.StateReceived
        mask = TxFlags.METADATA_FIELD_MASK | TxFlags.HasByteData | TxFlags.HasProofData
        date_updated = 1
        with SynchronousWriter() as writer:
            self.store.update_flags([ (tx_hash, flags, mask, date_updated) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Verify the state flag is correctly added via the mask.
        _tx_hash, flags_get, _metadata = self.store.read_metadata(tx_hashes=[tx_hash])[0]
        expected_flags |= TxFlags.StateReceived
        assert expected_flags == flags_get, \
            f"{TxFlags.to_repr(expected_flags)} != {TxFlags.to_repr(flags_get)}"

        flags = TxFlags.StateReceived
        mask = TxFlags.Unset
        date_updated = 1
        with SynchronousWriter() as writer:
            self.store.update_flags([ (tx_hash, flags, mask, date_updated) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Verify the state flag is correctly set via the mask.
        _tx_hash, flags, _metadata = self.store.read_metadata(tx_hashes=[tx_hash])[0]
        assert TxFlags.StateReceived == flags

    @pytest.mark.timeout(8)
    def test_delete(self) -> None:
        to_add = []
        for i in range(10):
            bytedata = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(bytedata)
            metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
            to_add.append((tx_hash, metadata, bytedata, TxFlags.Unset, None))
        with SynchronousWriter() as writer:
            self.store.create(to_add, completion_callback=writer.get_callback())
            assert writer.succeeded()

        add_hashes = set(t[0] for t in to_add)
        get_hashes = set(self._get_store_hashes())
        assert add_hashes == get_hashes
        with SynchronousWriter() as writer:
            self.store.delete(add_hashes, completion_callback=writer.get_callback())
            assert writer.succeeded()

        get_hashes = self._get_store_hashes()
        assert 0 == len(get_hashes)

    @pytest.mark.timeout(8)
    def test_get_all_pending(self):
        get_tx_hashes = set([])
        for tx_hex in (tx_hex_1, tx_hex_2):
            bytedata = bytes.fromhex(tx_hex)
            tx_hash = bitcoinx.double_sha256(bytedata)
            metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
            with SynchronousWriter() as writer:
                self.store.create([ (tx_hash, metadata, bytedata, TxFlags.Unset, None) ],
                    completion_callback=writer.get_callback())
                assert writer.succeeded()
            get_tx_hashes.add(tx_hash)

        result_tx_hashes = set(self._get_store_hashes())
        assert get_tx_hashes == result_tx_hashes

    @pytest.mark.timeout(8)
    def test_get(self):
        bytedata = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(bytedata)
        metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
        with SynchronousWriter() as writer:
            self.store.create([ (tx_hash, metadata, bytedata, TxFlags.Unset, None) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        assert tx_hash in self._get_store_hashes()
        assert self.store.read(tx_hashes=[tx_hash])
        assert self.store.read(TxFlags.HasByteData, TxFlags.HasByteData, [tx_hash])

    @pytest.mark.timeout(8)
    def test_read_metadata(self) -> None:
        # We're going to add five matches and look for two of them, checking that we do not match
        # unwanted rows.
        all_tx_hashes = []
        datas = []
        for i in range(5):
            bytedata = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(bytedata)
            metadata = TxData(height=i*100, fee=i*1000, position=None, date_added=1, date_updated=1)
            datas.append((tx_hash, metadata, bytedata, TxFlags.Unset, None))
            all_tx_hashes.append(tx_hash)
        with SynchronousWriter() as writer:
            self.store.create(datas, completion_callback=writer.get_callback())
            assert writer.succeeded()

        # We also ask for a dud tx_hash that won't get matched.
        select_tx_hashes = [ all_tx_hashes[0], all_tx_hashes[3], b"12121212" ]
        rowdatas = self.store.read_metadata(tx_hashes=select_tx_hashes)
        # Check that the two valid matches are there and their values match the projected values.
        assert len(rowdatas) == 2
        for rowdata in rowdatas:
            tx_hash = rowdata[0]
            tx_flags = rowdata[1]
            metadata = rowdata[2]
            rowidx = all_tx_hashes.index(tx_hash)
            assert metadata.height == rowidx * 100
            assert metadata.fee == rowidx * 1000
            assert metadata.position is None

    @pytest.mark.timeout(8)
    def test_update_metadata(self) -> None:
        # We're going to add five matches and look for two of them, checking that we do not match
        # unwanted rows.
        tx_hashes = []
        datas = []
        for i in range(5):
            bytedata = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(bytedata)
            metadata = TxData(height=i*100, fee=i*1000, position=None, date_added=1, date_updated=1)
            datas.append((tx_hash, metadata, bytedata, TxFlags.Unset, None))
            tx_hashes.append(tx_hash)
        with SynchronousWriter() as writer:
            self.store.create(datas, completion_callback=writer.get_callback())
            assert writer.succeeded()

        updates = []
        for i in range(5):
            tx_hash = tx_hashes[i]
            metadata = TxData(height=i*200, fee=i*2000, position=None, date_added=1, date_updated=1)
            updates.append((tx_hash, metadata, TxFlags.HasHeight | TxFlags.HasFee))
        with SynchronousWriter() as writer:
            self.store.update_metadata(updates, completion_callback=writer.get_callback())
            assert writer.succeeded()

        # We also ask for a dud tx_hash that won't get matched.
        select_tx_hashes = [ tx_hashes[0], tx_hashes[3], b"12121212" ]
        rowdatas = self.store.read_metadata(tx_hashes=select_tx_hashes)
        # Check that the two valid matches are there and their values match the projected values.
        assert len(rowdatas) == 2
        for rowdata in rowdatas:
            tx_hash = rowdata[0]
            tx_flags = rowdata[1]
            metadata = rowdata[2]
            rowidx = tx_hashes.index(tx_hash)
            assert metadata.height == rowidx * 200
            assert metadata.fee == rowidx * 2000
            assert metadata.position is None

    @pytest.mark.timeout(8)
    def test_read(self):
        to_add = []
        for i in range(10):
            tx_bytes = os.urandom(10)
            tx_hash = bitcoinx.double_sha256(tx_bytes)
            tx_data = TxData(height=None, fee=2, position=None, date_added=1, date_updated=1)
            to_add.append((tx_hash, tx_data, tx_bytes, TxFlags.HasFee, None))
        with SynchronousWriter() as writer:
            self.store.create(to_add, completion_callback=writer.get_callback())
            assert writer.succeeded()

        # Test the first "add" hash is matched.
        matches = self.store.read(tx_hashes=[to_add[0][0]])
        assert to_add[0][0] == matches[0][0]

        # Test no id is matched.
        matches = self.store.read(tx_hashes=[b"aaaa"])
        assert 0 == len(matches)

        # Test flag and mask combinations.
        matches = self.store.read(flags=TxFlags.HasFee)
        assert 10 == len(matches)

        matches = self.store.read(flags=TxFlags.Unset, mask=TxFlags.HasHeight)
        assert 10 == len(matches)

        matches = self.store.read(flags=TxFlags.HasFee, mask=TxFlags.HasFee)
        assert 10 == len(matches)

        matches = self.store.read(flags=TxFlags.Unset, mask=TxFlags.HasFee)
        assert 0 == len(matches)

    @pytest.mark.timeout(8)
    def test_proof(self):
        bytedata = os.urandom(10)
        tx_hash = bitcoinx.double_sha256(bytedata)
        metadata = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)
        with SynchronousWriter() as writer:
            self.store.create([ (tx_hash, metadata, bytedata, 0, None) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        position1 = 10
        merkle_branch1 = [ os.urandom(32) for i in range(10) ]
        proof = TxProof(position1, merkle_branch1)
        date_updated = 1
        with SynchronousWriter() as writer:
            self.store.update_proof([ (tx_hash, proof, date_updated) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        rows = self.store.read_proof([ self.tx_hash ])
        assert len(rows) == 0

        db_tx_hash, (tx_position2, merkle_branch2) = self.store.read_proof([ tx_hash ])[0]
        assert db_tx_hash == tx_hash
        assert position1 == tx_position2
        assert merkle_branch1 == merkle_branch2

    @pytest.mark.timeout(8)
    def test_labels(self):
        bytedata_1 = os.urandom(10)
        tx_hash_1 = bitcoinx.double_sha256(bytedata_1)
        metadata_1 = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)

        bytedata_2 = os.urandom(10)
        tx_hash_2 = bitcoinx.double_sha256(bytedata_2)
        metadata_2 = TxData(height=1, fee=2, position=None, date_added=1, date_updated=1)

        with SynchronousWriter() as writer:
            self.store.create([ (tx_hash_1, metadata_1, bytedata_1, 0, None),
                    (tx_hash_2, metadata_2, bytedata_2, 0, None) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        with SynchronousWriter() as writer:
            self.store.update_descriptions([ ("tx 1", tx_hash_1) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        rows = self.store.read_descriptions()
        assert len(rows) == 1
        assert len([r[1] == "tx 1" for r in rows if r[0] == tx_hash_1]) == 1

        with SynchronousWriter() as writer:
            self.store.update_descriptions([ (None, tx_hash_1), ("tx 2", tx_hash_2) ],
                completion_callback=writer.get_callback())
            assert writer.succeeded()

        rows = self.store.read_descriptions([ tx_hash_2 ])
        assert len(rows) == 1
        assert rows[0][0] == tx_hash_2 and rows[0][1] == "tx 2"

        # Reading entries for a non-existent ...
        rows = self.store.read_descriptions([ self.tx_hash ])
        assert len(rows) == 0


@pytest.mark.timeout(8)
def test_table_transactionoutputs_crud(db_context: DatabaseContext) -> None:
    table = TransactionOutputTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    TX_BYTES = os.urandom(10)
    TX_HASH = bitcoinx.double_sha256(TX_BYTES)
    TX_INDEX = 1
    TXOUT_FLAGS = 1 << 15
    KEYINSTANCE_ID = 1
    ACCOUNT_ID = 10
    MASTERKEY_ID = 20
    DERIVATION_DATA1 = b'111'
    DERIVATION_DATA2 = b'222'
    SCRIPT_TYPE = 40

    line1 = (TX_HASH, TX_INDEX, 100, KEYINSTANCE_ID, TXOUT_FLAGS)
    line2 = (TX_HASH, TX_INDEX+1, 200, KEYINSTANCE_ID, TXOUT_FLAGS)

    # No effect: The transactionoutput foreign key constraint will fail as the transactionoutput
    # does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the transaction foreign key constraint by creating the transaction.
    transaction_table = TransactionTable(db_context)
    with SynchronousWriter() as writer:
        transaction_table.create([ (TX_HASH, TxData(height=1, fee=2, position=None, date_added=1,
                date_updated=1), TX_BYTES, TxFlags.HasByteData|TxFlags.HasFee|TxFlags.HasHeight,
                None) ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    masterkey_table = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        masterkey_table.create([ (MASTERKEY_ID, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the account foreign key constraint by creating the account.
    account_table = AccountTable(db_context)
    with SynchronousWriter() as writer:
        account_table.create([ (ACCOUNT_ID, MASTERKEY_ID, ScriptType.P2PKH, 'name') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the keyinstance foreign key constraint by creating the keyinstance.
    keyinstance_table = KeyInstanceTable(db_context)
    with SynchronousWriter() as writer:
        keyinstance_table.create([ (KEYINSTANCE_ID, ACCOUNT_ID, MASTERKEY_ID,
            DerivationType.BIP32, DERIVATION_DATA1, SCRIPT_TYPE, True, None) ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the first row.
    with SynchronousWriter() as writer:
        table.create([ line1 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Create the second row.
    with SynchronousWriter() as writer:
        table.create([ line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line == line1 ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line == line2 ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update_flags([ (TransactionOutputFlag.IS_SPENT, line2[0], line2[1])], date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line[0:2] == line1[0:2] ][0]
    db_line2 = [ db_line for db_line in db_lines if db_line[0:2] == line2[0:2] ][0]
    assert db_line2.flags == TransactionOutputFlag.IS_SPENT

    db_lines = table.read(mask=~TransactionOutputFlag.IS_SPENT)
    assert 1 == len(db_lines)
    assert db_lines[0].flags & TransactionOutputFlag.IS_SPENT == 0

    db_lines = table.read(mask=TransactionOutputFlag.IS_SPENT)
    assert 1 == len(db_lines)
    assert db_lines[0].flags & TransactionOutputFlag.IS_SPENT == TransactionOutputFlag.IS_SPENT

    with SynchronousWriter() as writer:
        table.delete([ line2[0:2] ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0][0:2] == line1[0:2]


@pytest.mark.timeout(8)
def test_table_transactiondeltas_crud(db_context: DatabaseContext) -> None:
    table = TransactionDeltaTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    TX_BYTES = os.urandom(10)
    TX_HASH = bitcoinx.double_sha256(TX_BYTES)
    TX_INDEX = 1
    TXOUT_FLAGS = 1 << 15
    KEYINSTANCE_ID = 1
    ACCOUNT_ID = 10
    MASTERKEY_ID = 20
    DERIVATION_DATA = b'111'
    SCRIPT_TYPE = 40

    TX_BYTES2 = os.urandom(10)
    TX_HASH2 = bitcoinx.double_sha256(TX_BYTES2)

    LINE_COUNT = 3
    line1 = TransactionDeltaRow(TX_HASH, KEYINSTANCE_ID, 100)
    line2 = TransactionDeltaRow(TX_HASH, KEYINSTANCE_ID+1, 100)

    # No effect: The transactionoutput foreign key constraint will fail as the transactionoutput
    # does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the transaction foreign key constraint by creating the transaction.
    transaction_table = TransactionTable(db_context)
    with SynchronousWriter() as writer:
        transaction_table.create([
                (TX_HASH, TxData(height=1, fee=2, position=None, date_added=1,
                date_updated=1), TX_BYTES, TxFlags.HasByteData|TxFlags.HasFee|TxFlags.HasHeight,
                "tx 1"),
                (TX_HASH2, TxData(height=1, fee=2, position=None, date_added=1,
                date_updated=1), TX_BYTES2, TxFlags.HasByteData|TxFlags.HasFee|TxFlags.HasHeight,
                None)
            ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    masterkey_table = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        masterkey_table.create([ (MASTERKEY_ID, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the account foreign key constraint by creating the account.
    account_table = AccountTable(db_context)
    with SynchronousWriter() as writer:
        account_table.create([ (ACCOUNT_ID, MASTERKEY_ID, ScriptType.P2PKH, 'name') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the keyinstance foreign key constraint by creating the keyinstance.
    keyinstance_table = KeyInstanceTable(db_context)
    with SynchronousWriter() as writer:
        entries = [ (KEYINSTANCE_ID+i, ACCOUNT_ID, MASTERKEY_ID, DerivationType.BIP32,
            DERIVATION_DATA, SCRIPT_TYPE, True, None) for i in range(LINE_COUNT) ]
        keyinstance_table.create(entries, completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.create([ line1, line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line == line1 ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line == line2 ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update([ (20, line2[0], line2[1]) ], date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line2 = [ db_line for db_line in db_lines if db_line[0:2] == line2[0:2] ][0]
    assert db_line2[2] == 20

    line2_delta = TransactionDeltaRow(line2.tx_hash, line2.keyinstance_id, 200)
    line3 = TransactionDeltaRow(TX_HASH, KEYINSTANCE_ID+2, 999)
    with SynchronousWriter() as writer:
        table.create_or_update_relative_values([ line2_delta, line3 ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 3 == len(db_lines)
    db_line2 = [ db_line for db_line in db_lines if db_line[0:2] == line2[0:2] ][0]
    assert db_line2[2] == 20 + 200
    db_line3 = [ db_line for db_line in db_lines if db_line[0:2] == line3[0:2] ][0]
    assert db_line3[2] == line3[2]

    with SynchronousWriter() as writer:
        table.delete([ line2[0:2], line3[0:2] ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0][0:2] == line1[0:2]

    drows = table.read_descriptions(ACCOUNT_ID)
    assert len(drows) == 1
    assert drows[0] == (TX_HASH, "tx 1")


@pytest.mark.timeout(8)
def test_table_paymentrequests_crud(db_context: DatabaseContext) -> None:
    table = PaymentRequestTable(db_context)
    assert [] == table.read()

    table._get_current_timestamp = lambda: 10

    TX_BYTES = os.urandom(10)
    TX_HASH = bitcoinx.double_sha256(TX_BYTES)
    TX_INDEX = 1
    TXOUT_FLAGS = 1 << 15
    KEYINSTANCE_ID = 1
    ACCOUNT_ID = 10
    MASTERKEY_ID = 20
    DERIVATION_DATA = b'111'
    SCRIPT_TYPE = 40

    TX_BYTES2 = os.urandom(10)
    TX_HASH2 = bitcoinx.double_sha256(TX_BYTES2)

    LINE_COUNT = 3
    line1 = PaymentRequestRow(1, KEYINSTANCE_ID, PaymentState.PAID, None, None, "desc",
        table._get_current_timestamp())
    line2 = PaymentRequestRow(2, KEYINSTANCE_ID+1, PaymentState.UNPAID, 100, 60*60, None,
        table._get_current_timestamp())

    # No effect: The transactionoutput foreign key constraint will fail as the key instance
    # does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    masterkey_table = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        masterkey_table.create([ (MASTERKEY_ID, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the account foreign key constraint by creating the account.
    account_table = AccountTable(db_context)
    with SynchronousWriter() as writer:
        account_table.create([ (ACCOUNT_ID, MASTERKEY_ID, ScriptType.P2PKH, 'name') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the keyinstance foreign key constraint by creating the keyinstance.
    keyinstance_table = KeyInstanceTable(db_context)
    with SynchronousWriter() as writer:
        entries = [ (KEYINSTANCE_ID+i, ACCOUNT_ID, MASTERKEY_ID, DerivationType.BIP32,
            DERIVATION_DATA, SCRIPT_TYPE, True, None) for i in range(LINE_COUNT) ]
        keyinstance_table.create(entries, completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.create([ line1, line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line == line1 ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line == line2 ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update([ (PaymentState.UNKNOWN, 20, 999, "newdesc",
            line2.paymentrequest_id) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line2 = [ db_line for db_line in db_lines
        if db_line.paymentrequest_id == line2.paymentrequest_id ][0]
    assert db_line2.value == 20
    assert db_line2.state == PaymentState.UNKNOWN
    assert db_line2.description == "newdesc"
    assert db_line2.expiration == 999

    # Account does not exist.
    db_lines = table.read(1000)
    assert 0 == len(db_lines)

    # This account is matched.
    db_lines = table.read(ACCOUNT_ID)
    assert 2 == len(db_lines)

    with SynchronousWriter() as writer:
        table.delete([ (line2.paymentrequest_id,) ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0].paymentrequest_id == line1.paymentrequest_id


@pytest.mark.timeout(8)
def test_table_walletevents_crud(db_context: DatabaseContext) -> None:
    table = WalletEventTable(db_context)

    table._get_current_timestamp = lambda: 10

    MASTERKEY_ID = 1
    ACCOUNT_ID = 1

    line1 = WalletEventRow(1, WalletEventType.SEED_BACKUP_REMINDER, ACCOUNT_ID,
        WalletEventFlag.FEATURED | WalletEventFlag.UNREAD, table._get_current_timestamp())
    line2 = WalletEventRow(2, WalletEventType.SEED_BACKUP_REMINDER, None,
        WalletEventFlag.FEATURED | WalletEventFlag.UNREAD, table._get_current_timestamp())

    # No effect: The transactionoutput foreign key constraint will fail as the key instance
    # does not exist.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    # Satisfy the masterkey foreign key constraint by creating the masterkey.
    masterkey_table = MasterKeyTable(db_context)
    with SynchronousWriter() as writer:
        masterkey_table.create([ (MASTERKEY_ID, None, 2, b'111') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    # Satisfy the account foreign key constraint by creating the account.
    account_table = AccountTable(db_context)
    with SynchronousWriter() as writer:
        account_table.create([ (ACCOUNT_ID, MASTERKEY_ID, ScriptType.P2PKH, 'name') ],
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    with SynchronousWriter() as writer:
        table.create([ line1, line2 ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    # No effect: The primary key constraint will prevent any conflicting entry from being added.
    with pytest.raises(sqlite3.IntegrityError):
        with SynchronousWriter() as writer:
            table.create([ line1 ], completion_callback=writer.get_callback())
            assert not writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line1 = [ db_line for db_line in db_lines if db_line == line1 ][0]
    assert line1 == db_line1
    db_line2 = [ db_line for db_line in db_lines if db_line == line2 ][0]
    assert line2 == db_line2

    date_updated = 20

    with SynchronousWriter() as writer:
        table.update_flags([ (WalletEventFlag.UNREAD, line2.event_id) ],
            date_updated,
            completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 2 == len(db_lines)
    db_line2 = [ db_line for db_line in db_lines
        if db_line.event_id == line2.event_id ][0]
    assert db_line2.event_flags == WalletEventFlag.UNREAD

    # Account does not exist.
    db_lines = table.read(1000)
    assert 0 == len(db_lines)

    # This account is matched.
    db_lines = table.read(ACCOUNT_ID)
    assert 1 == len(db_lines)

    with SynchronousWriter() as writer:
        table.delete([ (line2.event_id,) ], completion_callback=writer.get_callback())
        assert writer.succeeded()

    db_lines = table.read()
    assert 1 == len(db_lines)
    assert db_lines[0].event_id == line1.event_id


