#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2014 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import json
import os
import time
from typing import Any, List, Optional, Tuple, Dict, TYPE_CHECKING
import urllib.parse

from .bip276 import bip276_encode, BIP276Network, PREFIX_SCRIPT
from bitcoinx import TxOutput, Script
import certifi
import requests

from .constants import RECEIVING_SUBPATH, PaymentState
from .exceptions import FileImportFailed, FileImportFailedEncrypted, Bip270Exception
from .logs import logs
from .networks import Net, SVScalingTestnet, SVTestnet, SVMainnet, SVRegTestnet
from .wallet_database.tables import PaymentRequestRow


if TYPE_CHECKING:
    from electrumsv.wallet import DeterministicAccount

logger = logs.get_logger("paymentrequest")

REQUEST_HEADERS = {
    'Accept': 'application/bitcoinsv-paymentrequest',
    'User-Agent': 'ElectrumSV'
}
ACK_HEADERS = {
    'Content-Type': 'application/bitcoinsv-payment',
    'Accept': 'application/bitcoinsv-paymentack',
    'User-Agent': 'ElectrumSV'
}

# Used for requests.
ca_path = certifi.where()



class Output:
    # FIXME: this should either be removed in favour of TxOutput, or be a lighter wrapper
    # around it.

    def __init__(self, script: Script, amount: Optional[int]=None,
                 description: Optional[str]=None):
        self.script = script
        # TODO: Must not have a JSON string length of 100 bytes.
        if description is not None:
            description_json = json.dumps(description)
            if len(description_json) > 100:
                raise Bip270Exception("Output description too long")
        self.description = description
        self.amount = amount

    def to_tx_output(self):
        return TxOutput(self.amount, self.script)

    @classmethod
    def from_dict(cls, data: dict) -> 'Output':
        if 'script' not in data:
            raise Bip270Exception("Missing required 'script' field")
        script_hex = data['script']

        amount = data.get('amount')
        if amount is not None and type(amount) is not int:
            raise Bip270Exception("Invalid 'amount' field")

        description = data.get('description')
        if description is not None and type(description) is not str:
            raise Bip270Exception("Invalid 'description' field")

        return cls(Script.from_hex(script_hex), amount, description)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            'script': self.script.to_hex(),
        }
        if self.amount and type(self.amount) is int:
            data['amount'] = self.amount
        if self.description:
            data['description'] = self.description
        return data

    @classmethod
    def from_json(cls, s: str) -> 'Output':
        data = json.loads(s)
        return cls.from_dict(data)

    def to_json(self) -> str:
        data = self.to_dict()
        return json.dumps(data)


class PaymentRequest:
    MAXIMUM_JSON_LENGTH = 10 * 1000 * 1000

    def __init__(self, outputs, creation_timestamp=None, expiration_timestamp=None, memo=None,
                 payment_url=None, merchant_data=None):
        # This is only used if there is a requestor identity (old openalias, needs rewrite).
        self.id = os.urandom(16).hex()
        # This is related to identity.
        self.requestor = None # known after verify
        self.tx = None

        self.outputs = outputs
        if creation_timestamp is not None:
            creation_timestamp = int(creation_timestamp)
        else:
            creation_timestamp = int(time.time())
        self.creation_timestamp = creation_timestamp
        if expiration_timestamp is not None:
            expiration_timestamp = int(expiration_timestamp)
        self.expiration_timestamp = expiration_timestamp
        self.memo = memo
        self.payment_url = payment_url
        self.merchant_data = merchant_data

    def __str__(self) -> str:
        return self.to_json()

    @classmethod
    def from_wallet_entry(cls, account: 'DeterministicAccount',
            pr: PaymentRequestRow) -> 'PaymentRequest':
        script = account.get_script_for_id(pr.keyinstance_id)
        date_expiry = None
        if pr.expiration is not None:
            date_expiry = pr.date_created + pr.expiration
        outputs = [ Output(script, pr.value) ]
        return cls(outputs, pr.date_created, date_expiry, pr.description)

    @classmethod
    def from_json(cls, s: str) -> 'PaymentRequest':
        if len(s) > cls.MAXIMUM_JSON_LENGTH:
            raise Bip270Exception(f"Invalid payment request, too large")

        d = json.loads(s)

        network = d.get('network')
        if network != 'bitcoin':
            raise Bip270Exception(f"Invalid json network: {network}")

        if 'outputs' not in d:
            raise Bip270Exception("Missing required json 'outputs' field")
        if type(d['outputs']) is not list:
            raise Bip270Exception("Invalid json 'outputs' field")

        outputs = []
        for ui_dict in d['outputs']:
            outputs.append(Output.from_dict(ui_dict))
        pr = cls(outputs)

        if 'creationTimestamp' not in d:
            raise Bip270Exception("Missing required json 'creationTimestamp' field")
        creation_timestamp = d['creationTimestamp']
        if type(creation_timestamp) is not int:
            raise Bip270Exception("Invalid json 'creationTimestamp' field")
        pr.creation_timestamp = creation_timestamp

        expiration_timestamp = d.get('expirationTimestamp')
        if expiration_timestamp is not None and type(expiration_timestamp) is not int:
            raise Bip270Exception("Invalid json 'expirationTimestamp' field")
        pr.expiration_timestamp = expiration_timestamp

        memo = d.get('memo')
        if memo is not None and type(memo) is not str:
            raise Bip270Exception("Invalid json 'memo' field")
        pr.memo = memo

        payment_url = d.get('paymentUrl')
        if payment_url is not None and type(payment_url) is not str:
            raise Bip270Exception("Invalid json 'paymentUrl' field")
        pr.payment_url = payment_url

        merchant_data = d.get('merchantData')
        if merchant_data is not None and type(merchant_data) is not str:
            raise Bip270Exception("Invalid json 'merchantData' field")
        pr.merchant_data = merchant_data

        return pr

    def to_json(self) -> str:
        d = {}
        d['network'] = 'bitcoin'
        d['outputs'] = [output.to_dict() for output in self.outputs]  # type: ignore
        d['creationTimestamp'] = self.creation_timestamp
        if self.expiration_timestamp is not None:
            d['expirationTimestamp'] = self.expiration_timestamp
        if self.memo is not None:
            d['memo'] = self.memo
        if self.payment_url is not None:
            d['paymentUrl'] = self.payment_url
        if self.merchant_data is not None:
            d['merchantData'] = self.merchant_data
        return json.dumps(d)

    def is_pr(self) -> bool:
        return self.get_amount() != 0

    def verify(self, contacts) -> bool:
        self.requestor = None
        return True

    def has_expired(self) -> bool:
        return self.expiration_timestamp and self.expiration_timestamp < int(time.time())

    def get_expiration_date(self) -> int:
        return self.expiration_timestamp

    def get_amount(self) -> int:
        return sum(x.amount for x in self.outputs)

    def get_address(self) -> str:
        if isinstance(Net._net, SVMainnet):
            network = BIP276Network.NETWORK_MAINNET
        elif isinstance(Net._net, SVTestnet):
            network = BIP276Network.NETWORK_TESTNET
        elif isinstance(Net._net, SVScalingTestnet):
            network = BIP276Network.NETWORK_SCALINGTESTNET
        elif isinstance(Net._net, SVRegTestnet):
            network = BIP276Network.NETWORK_REGTEST
        else:
            raise Exception("unhandled network", Net)
        return bip276_encode(PREFIX_SCRIPT, bytes(self.outputs[0].script), network)

    def get_requestor(self) -> str:
        return self.requestor if self.requestor else self.get_address()

    def get_verify_status(self) -> str:
        return self.error if self.requestor else "No Signature"  # type: ignore

    def get_memo(self) -> str:
        return self.memo

    def get_id(self) -> str:
        return self.id if self.requestor else self.get_address()

    def get_outputs(self) -> List[TxOutput]:
        return [output.to_tx_output() for output in self.outputs]

    def send_payment(self, account: 'DeterministicAccount',
            transaction_hex: str) -> Tuple[bool, Optional[str]]:

        if not self.payment_url:
            return False, "no url"

        refund_key = account.get_fresh_keys(RECEIVING_SUBPATH, 1)[0]
        script_template = account.get_script_template_for_id(refund_key.keyinstance_id)

        payment_memo = "Paid using ElectrumSV"
        payment = Payment(self.merchant_data, transaction_hex, [], payment_memo)
        payment.refund_outputs.append(Output(script_template.to_script()))

        parsed_url = urllib.parse.urlparse(self.payment_url)
        response = self._make_request(parsed_url.geturl(), payment.to_json())
        if response is None:
            return False, "Payment Message/PaymentACK Failed"

        if response.get_status_code() != 200:
            # Propagate 'Bad request' (HTTP 400) messages to the user since they
            # contain valuable information.
            if response.get_status_code() == 400:
                return False, f"{response.get_reason()}: {response.get_content().decode('UTF-8')}"
            # Some other errors might display an entire HTML document.
            # Hide those and just display the name of the error code.
            return False, response.get_reason()
        try:
            payment_ack = PaymentACK.from_json(response.get_content())
        except Exception:
            return False, ("PaymentACK could not be processed. Payment was sent; "
                           "please manually verify that payment was received.")

        logger.debug("PaymentACK message received: %s", payment_ack.memo)
        return True, payment_ack.memo

    # The following function and classes is abstracted to allow unit testing.
    def _make_request(self, url, message):
        try:
            r = requests.post(url, data=message, headers=ACK_HEADERS, verify=ca_path)
        except requests.exceptions.SSLError:
            logger.exception("Payment Message/PaymentACK")
            return None

        return self._RequestsResponseWrapper(r)

    class _RequestsResponseWrapper:
        def __init__(self, response):
            self._response = response

        def get_status_code(self):
            return self._response.status_code

        def get_reason(self):
            return self._response.reason

        def get_content(self):
            return self._response.content


class Payment:
    MAXIMUM_JSON_LENGTH = 10 * 1000 * 1000

    def __init__(self, merchant_data: Any, transaction_hex: str, refund_outputs: List[Output],
                 memo: Optional[str]=None) -> None:
        self.merchant_data = merchant_data
        self.transaction_hex = transaction_hex
        self.refund_outputs = refund_outputs
        self.memo = memo

    @classmethod
    def from_dict(cls, data: dict) -> 'Payment':
        if 'merchantData' not in data:
            raise Bip270Exception("Missing required json 'merchantData' field")
        merchant_data = data['merchantData']

        if 'transaction' not in data:
            raise Bip270Exception("Missing required json 'transaction' field")
        transaction_hex = data['transaction']
        if type(transaction_hex) is not str:
            raise Bip270Exception("Invalid json 'transaction' field")

        if 'refundTo' not in data:
            raise Bip270Exception("Missing required json 'refundTo' field")
        refundTo = data['refundTo']
        if type(refundTo) is not list:
            raise Bip270Exception("Invalid json 'refundTo' field")
        refund_outputs = [ Output.from_dict(data) for data in refundTo ]

        memo = data.get('memo')
        if memo is not None and type(memo) is not str:
            raise Bip270Exception("Invalid json 'memo' field")

        return cls(merchant_data, transaction_hex, refund_outputs, memo)

    def to_dict(self) -> dict:
        data = {
            'merchantData': self.merchant_data,
            'transaction': self.transaction_hex,
            'refundTo': [ output.to_dict() for output in self.refund_outputs ],
        }
        if self.memo:
            data['memo'] = self.memo
        return data

    @classmethod
    def from_json(cls, s: str) -> 'Payment':
        if len(s) > cls.MAXIMUM_JSON_LENGTH:
            raise Bip270Exception(f"Invalid payment, too large")
        data = json.loads(s)
        return cls.from_dict(data)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class PaymentACK:
    MAXIMUM_JSON_LENGTH = 11 * 1000 * 1000

    def __init__(self, payment: Payment, memo: Optional[str] = None) -> None:
        self.payment = payment
        self.memo = memo

    def to_dict(self) -> Dict[str, Any]:
        data = {
            'payment': self.payment.to_json(),
        }
        if self.memo:
            data['memo'] = self.memo
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PaymentACK':
        if 'payment' not in data:
            raise Bip270Exception("Missing required json 'payment' field")

        memo = data.get('memo')
        if memo is not None and type(memo) is not str:
            raise Bip270Exception("Invalid json 'memo' field")

        payment = Payment.from_json(data['payment'])
        return cls(payment, memo)

    def to_json(self) -> str:
        data = self.to_dict()
        return json.dumps(data)

    @classmethod
    def from_json(cls, s: str) -> 'PaymentACK':
        if len(s) > cls.MAXIMUM_JSON_LENGTH:
            raise Bip270Exception(f"Invalid payment ACK, too large")
        data = json.loads(s)
        return cls.from_dict(data)


def get_payment_request(url: str) -> PaymentRequest:
    error = None
    response = None
    data: Any = None
    u = urllib.parse.urlparse(url)
    if u.scheme in ['http', 'https']:
        try:
            response = requests.request('GET', url, headers=REQUEST_HEADERS)
            response.raise_for_status()
            # Guard against `bitcoin:`-URIs with invalid payment request URLs
            if "Content-Type" not in response.headers \
            or response.headers["Content-Type"] != "application/bitcoin-paymentrequest":
                data = None
                error = "payment URL not pointing to a bitcoinSV payment request handling server"
            else:
                data = response.content
            logger.debug('fetched payment request \'%s\' (%d)', url, len(response.content))
        except requests.exceptions.RequestException:
            data = None
            if response is not None:
                error = response.content.decode()
            else:
                error = "payment URL not pointing to a valid server"
    elif u.scheme == 'file':
        try:
            with open(u.path, 'r', encoding='utf-8') as f:
                data = f.read()
        except IOError:
            data = None
            error = "payment URL not pointing to a valid file"
    else:
        error = f"unknown scheme {url}"

    if error:
        raise Bip270Exception(error)

    return PaymentRequest.from_json(data)


class InvoiceStore:
    def __init__(self, wallet_data: Dict[str, Any]) -> None:
        self._wallet_data = wallet_data
        self.invoices: Dict[str, PaymentRequest] = {}
        self.paid: Dict[str, str] = {}

        d = wallet_data.get('invoices', {})
        self.load(d)

    def set_paid(self, pr: PaymentRequest, tx_id: str) -> None:
        pr.tx = tx_id
        self.paid[tx_id] = pr.get_id()

    def load(self, d) -> None:
        for k, v in d.items():
            try:
                pr = PaymentRequest(bytes.fromhex(v.get('hex')))
                pr.tx = v.get('txid')
                pr.requestor = v.get('requestor')
                self.invoices[k] = pr
                if pr.tx:
                    self.paid[pr.tx] = k
            except Exception:
                continue

    def import_file(self, path) -> None:
        try:
            with open(path, 'r') as f:
                d = json.loads(f.read())
                self.load(d)
        except json.decoder.JSONDecodeError:
            logger.exception("")
            raise FileImportFailedEncrypted()
        except Exception:
            logger.exception("")
            raise FileImportFailed()
        self.save()

    def save(self) -> None:
        l = {}
        for k, pr in self.invoices.items():
            l[k] = {
                'requestor': pr.requestor,
                'txid': pr.tx
            }
        self._wallet_data['invoices'] = l

    def get_status(self, request_id: str) -> PaymentState:
        pr = self.get(request_id)
        if pr is None:
            logger.debug("[InvoiceStore] get_status() can't find pr for %s", request_id)
            return PaymentState.UNKNOWN
        if pr.tx is not None:
            return PaymentState.PAID
        if pr.has_expired():
            return PaymentState.EXPIRED
        return PaymentState.UNPAID

    def add(self, pr: PaymentRequest) -> str:
        request_id = pr.get_id()
        self.invoices[request_id] = pr
        self.save()
        return request_id

    def remove(self, request_id: str) -> None:
        paid_list = self.paid.items()
        for p in paid_list:
            if p[1] == request_id:
                self.paid.pop(p[0])
                break
        self.invoices.pop(request_id)
        self.save()

    def get(self, request_id: str) -> Optional[PaymentRequest]:
        return self.invoices.get(request_id)

    def sorted_list(self) -> List[PaymentRequest]:
        # sort
        return list(self.invoices.values())

    def unpaid_invoices(self) -> List[PaymentRequest]:
        return [invoice for key, invoice in self.invoices.items()
                if self.get_status(key) != PaymentState.PAID]
