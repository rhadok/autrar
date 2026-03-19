"""
minter.py — The actual blockchain interaction layer
====================================================
Plain English: This is where the real work happens.
It connects to Base chain, reads contracts, and fires transactions.
"""

import os
import json
import asyncio
import logging
from web3 import Web3
from web3.middleware import geth_poa_middleware
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

# ============================================================
# STANDARD NFT MINT ABI
# Plain English: ABI = the "menu" of functions a smart contract has.
# Most NFT contracts have a mint() function that looks like this.
# If a specific collection uses a different function name (e.g. claim()),
# you'd add it here or override per contract.
# ============================================================
STANDARD_MINT_ABI = [
    # mint(uint256 quantity) — most common
    {
        "inputs": [{"internalType": "uint256", "name": "quantity", "type": "uint256"}],
        "name": "mint",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # publicMint(uint256 quantity)
    {
        "inputs": [{"internalType": "uint256", "name": "quantity", "type": "uint256"}],
        "name": "publicMint",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # mintPublic(address to, uint256 quantity) — some use this
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "quantity", "type": "uint256"}
        ],
        "name": "mintPublic",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    # Read functions — to check if mint is live
    {
        "inputs": [],
        "name": "mintPrice",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "price",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "publicSaleActive",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "saleIsActive",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "maxSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    },
]

# ETH price oracle ABI (Chainlink)
ETH_PRICE_ABI = [
    {
        "inputs": [],
        "name": "latestAnswer",
        "outputs": [{"internalType": "int256", "name": "", "type": "int256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

# Chainlink ETH/USD feed on Base
ETH_USD_FEED = "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"


class NFTMinter:
    def __init__(self):
        # --- Connect to Base chain ---
        # Plain English: RPC = the door to the blockchain. Alchemy/Infura give you this for free.
        self.rpc_url = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)

        # --- Load your wallet ---
        # Plain English: Your private key signs transactions. NEVER share it. It lives in .env only.
        self.private_key = os.getenv("WALLET_PRIVATE_KEY")
        self.wallet_address = self.w3.eth.account.from_key(self.private_key).address

        log.info(f"✅ Connected to Base: {self.w3.is_connected()}")
        log.info(f"👛 Wallet: {self.wallet_address}")

    async def mint(self, contract_address: str, quantity: int = 1, max_price_eth: float = 0.01) -> dict:
        """
        Plain English: This is the function that actually buys/mints the NFT.
        It tries multiple common mint function names until one works.
        """
        try:
            contract_address = Web3.to_checksum_address(contract_address)
            contract = self.w3.eth.contract(address=contract_address, abi=STANDARD_MINT_ABI)

            # --- Figure out the mint price ---
            mint_price_wei = await self._get_mint_price(contract)
            total_value_wei = mint_price_wei * quantity
            total_value_eth = self.w3.from_wei(total_value_wei, "ether")

            log.info(f"Mint price: {total_value_eth} ETH for {quantity} NFT(s)")

            # --- Safety check: don't spend more than you said ---
            if float(total_value_eth) > max_price_eth:
                return {
                    "success": False,
                    "error": f"Price {total_value_eth:.6f} ETH exceeds your max of {max_price_eth} ETH"
                }

            # --- Get current gas ---
            gas_price = self.w3.eth.gas_price
            nonce = self.w3.eth.get_transaction_count(self.wallet_address)

            # --- Try different mint function names ---
            # Plain English: Different NFT projects name their function differently.
            # We try them all until one works.
            tx_hash = None
            last_error = None

            for mint_fn_name in ["mint", "publicMint", "mintPublic"]:
                try:
                    mint_fn = getattr(contract.functions, mint_fn_name)

                    if mint_fn_name == "mintPublic":
                        tx = mint_fn(self.wallet_address, quantity).build_transaction({
                            "from": self.wallet_address,
                            "value": total_value_wei,
                            "gas": 300000,
                            "gasPrice": int(gas_price * 1.2),  # 20% tip for speed
                            "nonce": nonce,
                            "chainId": 8453  # Base mainnet chain ID
                        })
                    else:
                        tx = mint_fn(quantity).build_transaction({
                            "from": self.wallet_address,
                            "value": total_value_wei,
                            "gas": 300000,
                            "gasPrice": int(gas_price * 1.2),
                            "nonce": nonce,
                            "chainId": 8453
                        })

                    # Sign with your private key
                    signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)

                    # FIRE 🔥
                    raw_tx_hash = self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)
                    tx_hash = raw_tx_hash.hex()
                    log.info(f"TX sent: {tx_hash}")
                    break

                except Exception as e:
                    last_error = str(e)
                    log.warning(f"mint fn '{mint_fn_name}' failed: {e}")
                    continue

            if not tx_hash:
                return {"success": False, "error": f"All mint functions failed. Last error: {last_error}"}

            # --- Wait for confirmation ---
            receipt = self.w3.eth.wait_for_transaction_receipt(raw_tx_hash, timeout=120)
            gas_used = receipt["gasUsed"]
            gas_cost_eth = self.w3.from_wei(gas_price * gas_used, "ether")
            total_cost = float(total_value_eth) + float(gas_cost_eth)

            if receipt["status"] == 1:
                return {
                    "success": True,
                    "tx_hash": tx_hash,
                    "gas_used": gas_used,
                    "total_cost_eth": total_cost
                }
            else:
                return {"success": False, "error": "Transaction reverted on-chain (mint may have failed)"}

        except Exception as e:
            log.error(f"Mint error: {e}")
            return {"success": False, "error": str(e)}

    async def _get_mint_price(self, contract) -> int:
        """Try common price getter function names. Return 0 if free mint."""
        for price_fn in ["mintPrice", "price"]:
            try:
                price = getattr(contract.functions, price_fn)().call()
                return price
            except:
                continue
        return 0  # Free mint or price not readable this way

    async def is_mint_live(self, contract_address: str) -> bool:
        """
        Plain English: Checks if a mint is open yet.
        The /watch command uses this every 5 seconds.
        Returns True = mint is open, go go go!
        """
        try:
            contract_address = Web3.to_checksum_address(contract_address)
            contract = self.w3.eth.contract(address=contract_address, abi=STANDARD_MINT_ABI)

            # Try reading sale status flags
            for status_fn in ["publicSaleActive", "saleIsActive"]:
                try:
                    is_active = getattr(contract.functions, status_fn)().call()
                    if is_active:
                        return True
                except:
                    continue

            # Fallback: try actually calling mint with 0 value and see if it errors
            # (some contracts don't have a status flag)
            try:
                contract.functions.mint(1).call({
                    "from": self.wallet_address,
                    "value": 0
                })
                return True  # If it didn't throw, it's callable
            except Exception as e:
                err = str(e).lower()
                # Sale not started errors (not live yet)
                if any(x in err for x in ["sale not active", "not started", "paused", "not live", "not open"]):
                    return False
                # Other errors (wrong price etc) — mint IS live, just needs value
                return True

        except Exception as e:
            log.warning(f"is_mint_live check failed: {e}")
            return False

    async def get_balance(self) -> dict:
        """Returns your ETH balance on Base + USD estimate."""
        balance_wei = self.w3.eth.get_balance(self.wallet_address)
        balance_eth = float(self.w3.from_wei(balance_wei, "ether"))

        # Get ETH price from Chainlink
        try:
            feed = self.w3.eth.contract(
                address=Web3.to_checksum_address(ETH_USD_FEED),
                abi=ETH_PRICE_ABI
            )
            eth_price_usd = feed.functions.latestAnswer().call() / 1e8
        except:
            eth_price_usd = 3000  # Fallback

        return {
            "eth": balance_eth,
            "usd_approx": balance_eth * eth_price_usd
        }

    async def get_gas_price(self) -> dict:
        """Returns current gas in gwei (slow/standard/fast)."""
        base_gas = self.w3.eth.gas_price
        gwei = self.w3.from_wei(base_gas, "gwei")
        return {
            "slow": round(float(gwei) * 0.8, 2),
            "standard": round(float(gwei), 2),
            "fast": round(float(gwei) * 1.3, 2),
}
                  
