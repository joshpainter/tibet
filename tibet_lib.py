import asyncio
import os
import sys
import time
import requests
from pathlib import Path
from typing import List

from blspy import AugSchemeMPL
from blspy import PrivateKey
from cdv.cmds.rpc import get_client
from cdv.cmds.sim_utils import SIMULATOR_ROOT_PATH
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.simulator.simulator_full_node_rpc_client import \
    SimulatorFullNodeRpcClient
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import INFINITE_COST
from chia.types.blockchain_format.program import Program
try:
    from chia.types.blockchain_format.serialized_program import SerializedProgram
except:
    from chia.types.blockchain_format.program import SerializedProgram
from chia.types.blockchain_format.sized_bytes import bytes32
from chia.types.coin_spend import CoinSpend
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.spend_bundle import SpendBundle
from chia.util.bech32m import bech32_decode
from chia.util.bech32m import bech32_encode
from chia.util.bech32m import convertbits
from chia.util.bech32m import decode_puzzle_hash
from chia.util.bech32m import encode_puzzle_hash
from chia.util.condition_tools import conditions_dict_for_solution
from chia.util.config import load_config
from chia.util.hash import std_hash
from chia.util.ints import uint16
from chia.util.ints import uint32
from chia.util.ints import uint64
from chia.wallet.cat_wallet.cat_utils import SpendableCAT
from chia.wallet.cat_wallet.cat_utils import construct_cat_puzzle
from chia.wallet.cat_wallet.cat_utils import get_innerpuzzle_from_puzzle
from chia.wallet.cat_wallet.cat_utils import \
    unsigned_spend_bundle_for_spendable_cats
from chia.wallet.derive_keys import master_sk_to_wallet_sk_unhardened
from chia.wallet.lineage_proof import LineageProof
from chia.wallet.puzzles.cat_loader import CAT_MOD
from chia.wallet.puzzles.cat_loader import CAT_MOD_HASH
from chia.wallet.puzzles.p2_conditions import puzzle_for_conditions
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import \
    DEFAULT_HIDDEN_PUZZLE_HASH
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import \
    calculate_synthetic_secret_key
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import \
    puzzle_for_pk
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import \
    puzzle_for_synthetic_public_key
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import \
    solution_for_delegated_puzzle
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_LAUNCHER
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    SINGLETON_LAUNCHER_HASH
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_MOD
from chia.wallet.puzzles.singleton_top_layer_v1_1 import SINGLETON_MOD_HASH
from chia.wallet.puzzles.singleton_top_layer_v1_1 import generate_launcher_coin
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    launch_conditions_and_coinsol
from chia.wallet.puzzles.singleton_top_layer_v1_1 import \
    lineage_proof_for_coinsol
from chia.wallet.puzzles.singleton_top_layer_v1_1 import puzzle_for_singleton
from chia.wallet.puzzles.singleton_top_layer_v1_1 import solution_for_singleton
from chia.wallet.puzzles.tails import GenesisById
from chia.wallet.sign_coin_spends import sign_coin_spends
from chia.wallet.trading.offer import OFFER_MOD
from chia.wallet.trading.offer import OFFER_MOD_HASH
from chia.wallet.trading.offer import Offer
from chia.wallet.util.puzzle_compression import compress_object_with_puzzles
from chia.wallet.util.puzzle_compression import decompress_object_with_puzzles
from chia.wallet.util.puzzle_compression import lowest_best_version
from chia_rs import run_chia_program
from clvm.casts import int_to_bytes

from clvm import SExp

from leaflet_client import LeafletFullNodeRpcClient
from cic.drivers.merkle_utils import build_merkle_tree

def load_clvm_hex(
    filename
) -> Program:
    clvm_hex = open(filename, "r").read().strip()
    assert len(clvm_hex) != 0
    
    clvm_blob = bytes.fromhex(clvm_hex)
    return SerializedProgram.from_bytes(clvm_blob).to_program()

ROUTER_MOD: Program = load_clvm_hex("clvm/router.clvm.hex")
LIQUIDITY_TAIL_MOD: Program = load_clvm_hex("clvm/liquidity_tail.clvm.hex")

P2_SINGLETON_FLASHLOAN_MOD: Program = load_clvm_hex("clvm/p2_singleton_flashloan.clvm.hex")
P2_MERKLE_TREE_MODIFIED_MOD: Program = load_clvm_hex("clvm/p2_merkle_tree_modified.clvm.hex")

PAIR_INNER_PUZZLE_MOD: Program = load_clvm_hex("clvm/pair_inner_puzzle.clvm.hex")
ADD_LIQUIDITY_MOD: Program = load_clvm_hex("clvm/add_liquidity.clvm.hex")
REMOVE_LIQUIDITY_MOD: Program = load_clvm_hex("clvm/remove_liquidity.clvm.hex")
SWAP_MOD: Program = load_clvm_hex("clvm/swap.clvm.hex")

# todo: debug remove unused hashes
ROUTER_MOD_HASH = ROUTER_MOD.get_tree_hash()
LIQUIDITY_TAIL_MOD_HASH: Program = LIQUIDITY_TAIL_MOD.get_tree_hash()
P2_SINGLETON_FLASHLOAN_MOD_HASH: Program = P2_SINGLETON_FLASHLOAN_MOD.get_tree_hash()
P2_MERKLE_TREE_MODIFIED_MOD_HASH: Program = P2_MERKLE_TREE_MODIFIED_MOD.get_tree_hash()
PAIR_INNER_PUZZLE_MOD_HASH: Program = PAIR_INNER_PUZZLE_MOD.get_tree_hash()
ADD_LIQUIDITY_MOD_HASH: Program = ADD_LIQUIDITY_MOD.get_tree_hash()
REMOVE_LIQUIDITY_MOD_HASH: Program = REMOVE_LIQUIDITY_MOD.get_tree_hash()
SWAP_MOD_HASH: Program = SWAP_MOD.get_tree_hash()

# can be overriden for in func calls
DEFAULT_RETURN_ADDRESS = "xch10d09t9eqpr2y34thcayk54sjz34qhyv3tmhrejjp6xxvj598sfds5z0xch"

ADD_LIQUIDITY_PUZZLE = ADD_LIQUIDITY_MOD.curry(
    CAT_MOD_HASH,
    LIQUIDITY_TAIL_MOD_HASH
)
ADD_LIQUIDITY_PUZZLE_HASH = ADD_LIQUIDITY_PUZZLE.get_tree_hash()

REMOVE_LIQUIDITY_PUZZLE = REMOVE_LIQUIDITY_MOD.curry(
    CAT_MOD_HASH,
    LIQUIDITY_TAIL_MOD_HASH
)
REMOVE_LIQUIDITY_PUZZLE_HASH = REMOVE_LIQUIDITY_PUZZLE.get_tree_hash()

SWAP_PUZZLE = SWAP_MOD.curry(993)
SWAP_PUZZLE_HASH = SWAP_PUZZLE.get_tree_hash()

MERKLE_ROOT, MERKLE_PROOFS = build_merkle_tree([
    ADD_LIQUIDITY_PUZZLE_HASH,
    REMOVE_LIQUIDITY_PUZZLE_HASH,
    SWAP_PUZZLE_HASH,
    DEFAULT_HIDDEN_PUZZLE_HASH
])

ROUTER_PUZZLE = ROUTER_MOD.curry(
    PAIR_INNER_PUZZLE_MOD_HASH,
    SINGLETON_MOD_HASH,
    P2_MERKLE_TREE_MODIFIED_MOD_HASH,
    P2_SINGLETON_FLASHLOAN_MOD_HASH
    CAT_MOD_HASH
    SETTLEMENT_PAYMENTS_MOD_HASH
    MERKLE_ROOT,
    SINGLETON_LAUNCHER_HASH,
    ROUTER_MOD_HASH
)

def get_pair_inner_inner_puzzle(singleton_launcher_id, tail_hash):
    return PAIR_INNER_PUZZLE_MOD.curry(
        P2_MERKLE_TREE_MODIFIED_MOD_HASH,
        (SINGLETON_MOD_HASH, (singleton_launcher_id, SINGLETON_LAUNCHER_HASH)),
        P2_SINGLETON_FLASHLOAN_MOD_HASH,
        OFFER_MOD_HASH,
        tail_hash,
    )


def get_pair_inner_puzzle(singleton_launcher_id, tail_hash, liquidity, xch_reserve, token_reserve):
    return P2_MERKLE_TREE_MODIFIED_MOD.curry(
        get_pair_inner_inner_puzzle(singleton_launcher_id, tail_hash),
        MERKLE_ROOT,
        (liquidity, (xch_reserve, token_reserve))
    )


def get_pair_puzzle(singleton_launcher_id, tail_hash, liquidity, xch_reserve, token_reserve):
    return puzzle_for_singleton(
        singleton_launcher_id,
        get_pair_inner_puzzle(singleton_launcher_id, tail_hash, liquidity, xch_reserve, token_reserve)
    )


def pair_liquidity_tail_puzzle(pair_launcher_id):
    return LIQUIDITY_TAIL_MOD.curry(
        (SINGLETON_MOD_HASH, (pair_launcher_id, SINGLETON_LAUNCHER_HASH))
    )

# https://github.com/Chia-Network/chia-blockchain/blob/main/chia/wallet/puzzles/singleton_top_layer_v1_1.py
def pay_to_singleton_flashloan_puzzle(launcher_id: bytes32) -> Program:
    return P2_SINGLETON_FLASHLOAN_MOD.curry(SINGLETON_MOD_HASH, launcher_id, SINGLETON_LAUNCHER_HASH)

# https://github.com/Chia-Network/chia-blockchain/blob/main/chia/wallet/puzzles/singleton_top_layer_v1_1.py
def solution_for_p2_singleton_flashloan(
    p2_singleton_coin: Coin,
    singleton_inner_puzhash: bytes32,
    extra_conditions = []
) -> Program:
    solution: Program = Program.to([singleton_inner_puzhash, p2_singleton_coin.name(), extra_conditions])
    return solution


async def get_full_node_client(
    chia_root,
    leaflet_url
) -> FullNodeRpcClient:
    node_client = None

    if leaflet_url is not None:
        # use leaflet by default
        node_client = LeafletFullNodeRpcClient(leaflet_url)
    else:
        root_path = Path(chia_root)

        config = load_config(root_path, "config.yaml")
        self_hostname = config["self_hostname"]
        rpc_port = config["full_node"]["rpc_port"]
        node_client: FullNodeRpcClient = await FullNodeRpcClient.create(
            self_hostname, uint16(rpc_port), root_path, config
        )
    
    await node_client.healthz()

    return node_client

async def get_sim_full_node_client(
    chia_root: str
) -> SimulatorFullNodeRpcClient:
    root_path = Path(chia_root)

    config = load_config(root_path, "config.yaml")
    self_hostname = config["self_hostname"]
    rpc_port = config["full_node"]["rpc_port"]
    node_client: SimulatorFullNodeRpcClient = await SimulatorFullNodeRpcClient.create(
        self_hostname, uint16(rpc_port), root_path, config
    )
    await node_client.healthz()

    return node_client

async def get_wallet_client(
    chia_root: str
) -> FullNodeRpcClient:
    root_path = Path(chia_root)

    config = load_config(root_path, "config.yaml")
    self_hostname = config["self_hostname"]
    rpc_port = config["wallet"]["rpc_port"]
    wallet_client: WalletRpcClient = await WalletRpcClient.create(
        self_hostname, uint16(rpc_port), root_path, config
    )
    await wallet_client.healthz()

    return wallet_client


async def launch_router_from_coin(parent_coin, parent_coin_puzzle, fee=0):
    comment: List[Tuple[str, str]] = [("tibet", "v1")]
    conds, launcher_coin_spend = launch_conditions_and_coinsol(parent_coin, get_router_puzzle(), comment, 1)
    if parent_coin.amount > fee + 1:
        conds.append(
            [
                ConditionOpcode.CREATE_COIN,
                parent_coin.puzzle_hash,
                parent_coin.amount - 1 - fee,
            ]
        )
    if fee > 0:
        conds.append([
            ConditionOpcode.RESERVE_FEE,
            fee
        ])

    p2_coin_spend = CoinSpend(
        parent_coin,
        parent_coin_puzzle,
        solution_for_delegated_puzzle(Program.to((1, conds)), [])
    )
        
    sb = SpendBundle(
        [launcher_coin_spend, p2_coin_spend],
        AugSchemeMPL.aggregate([])
    )
    launcher_id = launcher_coin_spend.coin.name().hex()
    
    return launcher_id, sb

async def create_test_cat(token_amount, coin, coin_puzzle):
    coin_id = coin.name()

    tail = GenesisById.construct([coin_id])
    tail_hash = tail.get_tree_hash()
    cat_inner_puzzle = coin_puzzle

    cat_puzzle = construct_cat_puzzle(CAT_MOD, tail_hash, cat_inner_puzzle)
    cat_puzzle_hash = cat_puzzle.get_tree_hash()

    cat_creation_tx = CoinSpend(
        coin,
        cat_inner_puzzle, # same as this coin's puzzle
        solution_for_delegated_puzzle(Program.to((1, [
            [ConditionOpcode.CREATE_COIN, cat_puzzle_hash, token_amount * 1000],
            [ConditionOpcode.CREATE_COIN, coin.puzzle_hash, coin.amount - token_amount * 1000],
        ])), [])
    )
    
    cat_coin = Coin(
        coin.name(), # parent
        cat_puzzle_hash,
        token_amount * 1000
    )

    cat_inner_solution = solution_for_delegated_puzzle(
        Program.to((1, [
            [ConditionOpcode.CREATE_COIN, 0, -113, tail, []],
            [ConditionOpcode.CREATE_COIN, cat_inner_puzzle.get_tree_hash(), cat_coin.amount]
        ])), []
    )

    cat_eve_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                cat_coin,
                tail_hash,
                cat_inner_puzzle,
                cat_inner_solution,
                limitations_program_reveal=tail,
            )
        ],
    )
    cat_eve_spend = cat_eve_spend_bundle.coin_spends[0]
    
    sb = SpendBundle(
        [cat_creation_tx, cat_eve_spend],
        AugSchemeMPL.aggregate([])
    )
    
    return tail_hash.hex(), sb


async def create_pair_from_coin(
    coin,
    coin_puzzle,
    tail_hash,
    router_launcher_id,
    current_router_coin,
    current_router_coin_creation_spend,
    fee=0
):
    lineage_proof = lineage_proof_for_coinsol(current_router_coin_creation_spend)
    
    router_inner_puzzle = get_router_puzzle()
    router_inner_solution = Program.to([
        current_router_coin.name(),
        tail_hash
    ])

    router_singleton_puzzle = puzzle_for_singleton(router_launcher_id, router_inner_puzzle)
    router_singleton_solution = solution_for_singleton(lineage_proof, current_router_coin.amount, router_inner_solution)
    router_singleton_spend = CoinSpend(current_router_coin, router_singleton_puzzle, router_singleton_solution)

    pair_launcher_coin = Coin(current_router_coin.name(), SINGLETON_LAUNCHER_HASH, 2)
    pair_puzzle = get_pair_puzzle(
        pair_launcher_coin.name(),
        tail_hash,
        0, 0, 0
    )

    comment: List[Tuple[str, str]] = []

    # launch_conditions_and_coinsol would not work here since we spend a coin with amount 2
    # and the solution says the amount is 1 *-*
    pair_launcher_solution = Program.to(
        [
            pair_puzzle.get_tree_hash(),
            1,
            comment,
        ]
    )
    assert_launcher_announcement = [
        ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT,
        std_hash(pair_launcher_coin.name() + pair_launcher_solution.get_tree_hash()),
    ]
    pair_launcher_spend = CoinSpend(
        pair_launcher_coin,
        SINGLETON_LAUNCHER,
        pair_launcher_solution,
    )

    # first condition is CREATE_COIN, which we took care of
    # important to note: router also takes care of assert_launcher_announcement, but we need it to link the fund spend to the bundle
    conds = []
    conds.append([ConditionOpcode.CREATE_COIN, coin.puzzle_hash, coin.amount - fee - 2])
    if fee > 0:
        conds.append([ConditionOpcode.RESERVE_FEE, fee])
    conds.append(assert_launcher_announcement)
    fund_spend = CoinSpend(
        coin,
        coin_puzzle,
        solution_for_delegated_puzzle(Program.to((1, conds)), [])
    )
    
    pair_launcher_id = Coin(current_router_coin.name(), SINGLETON_LAUNCHER_HASH, 2).name().hex()
    sb = SpendBundle(
        [router_singleton_spend, pair_launcher_spend, fund_spend],
        AugSchemeMPL.aggregate([])
    )
    return pair_launcher_id, sb

async def sync_router(full_node_client, last_router_id):
    new_pairs = []
    coin_record = await full_node_client.get_coin_record_by_name(last_router_id)
    if not coin_record.spent:
        # hack
        current_router_coin, creation_spend, _ = await sync_router(full_node_client, coin_record.coin.parent_coin_info)
        return current_router_coin, creation_spend, []
    
    router_puzzle_hash = get_router_puzzle().get_tree_hash()

    while coin_record.spent:
        creation_spend = await full_node_client.get_puzzle_and_solution(last_router_id, coin_record.spent_block_index)
        _, conditions_dict, __ = conditions_dict_for_solution(
            creation_spend.puzzle_reveal,
            creation_spend.solution,
            INFINITE_COST
        )

        if coin_record.coin.puzzle_hash != SINGLETON_LAUNCHER_HASH:
            solution_program = creation_spend.solution.to_program()
            tail_hash = [_ for _ in solution_program.as_iter()][-1].as_python()[-1]
        
        for cwa in conditions_dict[ConditionOpcode.CREATE_COIN]:
            new_puzzle_hash = cwa.vars[0]
            new_amount = cwa.vars[1]

            if new_amount == b"\x01": # CREATE_COIN with amount=1 -> router recreated
                new_router_coin = Coin(last_router_id, new_puzzle_hash, 1)

                last_router_id = new_router_coin.name()
            elif new_amount == b"\x02": # CREATE_COIN with amount=2 -> pair launcher deployed
                assert new_puzzle_hash == SINGLETON_LAUNCHER_HASH
                
                pair_launcher_coin = Coin(creation_spend.coin.name(), new_puzzle_hash, 2)
                pair_launcher_id = pair_launcher_coin.name()
                
                new_pairs.append((tail_hash.hex(), pair_launcher_id.hex()))
            else:
                print("Someone did something extremely weird with the router - time to call the cops.")
                sys.exit(1)

        coin_record = await full_node_client.get_coin_record_by_name(last_router_id)
    
    return coin_record.coin, creation_spend, new_pairs


async def get_spend_bundle_in_mempool(full_node_client, coin):
    try:
        parent_id_hex = "0x" + coin.parent_coin_info.hex()
        r = requests.post("http://localhost:1337/get_mempool_item_by_parent_coin_info", json={
            "request_url": full_node_client.leaflet_url + "get_all_mempool_items",
            "parent_coin_info": parent_id_hex
        })

        j = r.json()
        if j["item"] is None:
            return None

        return SpendBundle.from_json_dict(j["item"])
    except:
        return await get_spend_bundle_in_mempool_full_node(full_node_client, coin.name())


async def get_spend_bundle_in_mempool_full_node(full_node_client, coin_id):
    items = await full_node_client.fetch("get_all_mempool_items", {})
    
    for sb_id, d in items["mempool_items"].items():
        sb = SpendBundle.from_json_dict(d["spend_bundle"])
        for cs in sb.coin_spends:
            if cs.coin.name() == coin_id:
                return sb

    return None


def get_coin_spend_from_sb(sb, coin_name):
    if sb is None:
        return None
    
    for cs in sb.coin_spends:
        if cs.coin.name() == coin_name:
            return cs

    return None


async def sync_pair(full_node_client, last_synced_coin_id, tail_hash):
    state = {
        "liquidity": 0,
        "xch_reserve": 0,
        "token_reserve": 0
    }
    coin_record = await full_node_client.get_coin_record_by_name(last_synced_coin_id)
    last_synced_coin = coin_record.coin
    creation_spend = None

    if coin_record.coin.puzzle_hash == SINGLETON_LAUNCHER_HASH:
        creation_spend = await full_node_client.get_puzzle_and_solution(last_synced_coin_id, coin_record.spent_block_index)
        _, conditions_dict, __ = conditions_dict_for_solution(
            creation_spend.puzzle_reveal,
            creation_spend.solution,
            INFINITE_COST
        )
        last_synced_coin = Coin(coin_record.coin.name(), conditions_dict[ConditionOpcode.CREATE_COIN][0].vars[0], 1)

    if not coin_record.spent:
        # hack
        current_pair_coin, creation_spend, state, sb_to_aggregate, last_synced_pair_id_on_blockchain = await sync_pair(full_node_client, coin_record.coin.parent_coin_info, tail_hash)
        return current_pair_coin, creation_spend, state, sb_to_aggregate, last_synced_pair_id_on_blockchain

    creation_spend = None
    while coin_record.spent:
        creation_spend = await full_node_client.get_puzzle_and_solution(last_synced_coin_id, coin_record.spent_block_index)
        _, conditions_dict, __ = conditions_dict_for_solution(
            creation_spend.puzzle_reveal,
            creation_spend.solution,
            INFINITE_COST
        )
        
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN, []):
            new_puzzle_hash = cwa.vars[0]
            new_amount = cwa.vars[1]

            if new_amount == b"\x01": # CREATE_COIN with amount=1 -> pair recreation
                last_synced_coin = Coin(last_synced_coin_id, new_puzzle_hash, 1)
                last_synced_coin_id = last_synced_coin.name()

        coin_record = await full_node_client.get_coin_record_by_name(last_synced_coin_id)
    
    last_synced_pair_id_on_blockchain = last_synced_coin_id
    # mempool - watch this aggregation!
    last_coin_on_chain = coin_record.coin 
    last_coin_on_chain_id = last_coin_on_chain.name()
    sb = await get_spend_bundle_in_mempool(full_node_client, last_coin_on_chain)
    sb_to_aggregate = sb

    coin_spend = get_coin_spend_from_sb(sb, last_coin_on_chain_id)
    while coin_spend != None:
        creation_spend = coin_spend
        _, conditions_dict, __ = conditions_dict_for_solution(
            creation_spend.puzzle_reveal,
            creation_spend.solution,
            INFINITE_COST
        )
        
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN, []):
            new_puzzle_hash = cwa.vars[0]
            new_amount = cwa.vars[1]

            if new_amount == b"\x01": # CREATE_COIN with amount=1 -> pair recreation
                last_synced_coin = Coin(last_synced_coin_id, new_puzzle_hash, 1)
                last_synced_coin_id = last_synced_coin.name()

        coin_spend = get_coin_spend_from_sb(sb, last_synced_coin_id)

    if creation_spend.coin.puzzle_hash == SINGLETON_LAUNCHER_HASH:
        return last_synced_coin, creation_spend, state, None, last_synced_coin.name()

    creation_spend_curried_args = creation_spend.puzzle_reveal.uncurry()[1].at("rf").uncurry()[1].at("rrf")
    liquidity = creation_spend_curried_args.at("f").as_int()
    xch_reserve = creation_spend_curried_args.at("rf").as_int()
    token_reserve = creation_spend_curried_args.at("rr").as_int()

    p2_merkle_solution = creation_spend.solution.to_program().at("rrf")
    merkle_proof = p2_merkle_solution.at("rf")
    params = p2_merkle_solution.at("rrf").ar("rf")

    print(merkle_proof)
    print(MERKLE_PROOFS)
    print(params)
    print("TODO: DEBUG")
    input()

    action = creation_spend_inner_solution.at("rf").as_int()
    params = creation_spend_inner_solution.at("rrf")
    
    if action == 0: # deposit liquidity
        token_amount = params.at("f").as_int()
        if liquidity == 0:
            xch_amount = params.at("rrrf").as_int()
            liquidity = token_amount
            xch_reserve = xch_amount
            token_reserve = token_amount
        else:
            liquidity += token_amount * liquidity // token_reserve
            xch_reserve += token_amount * xch_reserve // token_reserve
            token_reserve += token_amount
    elif action == 1: # remove liquidity
        liquidity_tokens_amount = params.at("f").as_int()
        xch_reserve -= liquidity_tokens_amount * xch_reserve // liquidity
        token_reserve -= liquidity_tokens_amount * token_reserve // liquidity
        liquidity -= liquidity_tokens_amount
    elif action == 2: # xch to token
        xch_amount = params.at("f").as_int()
        token_reserve -= (token_reserve * xch_amount * 993) // (1000 * xch_reserve + 993 * xch_amount)
        xch_reserve += xch_amount
    elif action == 3: # token to xch
        token_amount = params.at("f").as_int()
        xch_reserve -= (xch_reserve * token_amount * 993) // (1000 * token_reserve + 993 * token_amount)
        token_reserve += token_amount

    state = {
        "liquidity": liquidity,
        "xch_reserve": xch_reserve,
        "token_reserve": token_reserve
    }

    return last_synced_coin, creation_spend, state, sb_to_aggregate, last_synced_pair_id_on_blockchain


async def get_pair_reserve_info(
    full_node_client,
    pair_launcher_id,
    pair_coin,
    token_tail_hash,
    creation_spend,
    cached_sb
):
    puzzle_announcements_asserts = []
    _, conditions_dict, __ = conditions_dict_for_solution(
        creation_spend.puzzle_reveal,
        creation_spend.solution,
        INFINITE_COST
    )
    for cwa in conditions_dict.get(ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT, []):
        puzzle_announcements_asserts.append(cwa.vars[0])

    if len(puzzle_announcements_asserts) == 0:
        return None, None, None # new pair

    p2_singleton_puzzle = pay_to_singleton_flashloan_puzzle(pair_launcher_id)
    p2_singleton_puzzle_hash = p2_singleton_puzzle.get_tree_hash()
    p2_singleton_cat_puzzle = construct_cat_puzzle(CAT_MOD, token_tail_hash, p2_singleton_puzzle)
    p2_singleton_cat_puzzle_hash = p2_singleton_cat_puzzle.get_tree_hash()

    spends = []

    if cached_sb is None:
        coin_record = await full_node_client.get_coin_record_by_name(pair_coin.name())
        block_record = await full_node_client.get_block_record_by_height(coin_record.confirmed_block_index)
        spends = await full_node_client.get_block_spends(block_record.header_hash)
    else:
        spends = cached_sb.coin_spends

    xch_reserve_coin = None
    token_reserve_coin = None
    token_reserve_lineage_proof = []
    for spend in spends:
        _, conditions_dict, __ = conditions_dict_for_solution(
            spend.puzzle_reveal,
            spend.solution,
            INFINITE_COST
        )

        for cwa in conditions_dict.get(ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT, []):
            ann_hash = std_hash(spend.coin.puzzle_hash + cwa.vars[0])
            if ann_hash in puzzle_announcements_asserts:
                amount = 0
                for cwa2 in conditions_dict[ConditionOpcode.CREATE_COIN]:
                    if cwa2.vars[0] in [p2_singleton_puzzle_hash, p2_singleton_cat_puzzle_hash]:
                        amount = SExp.to(cwa2.vars[1]).as_int()
                if spend.coin.puzzle_hash == OFFER_MOD_HASH:
                    xch_reserve_coin = Coin(spend.coin.name(), p2_singleton_puzzle_hash, amount)
                else: # OFFER_MOD_HASH but wrapped in CAT puzzle
                    token_reserve_coin = Coin(spend.coin.name(), p2_singleton_cat_puzzle_hash, amount)
                    token_reserve_lineage_proof = [
                        spend.coin.parent_coin_info,
                        OFFER_MOD_HASH,
                        spend.coin.amount
                    ]
                break
    
    return xch_reserve_coin, token_reserve_coin, token_reserve_lineage_proof


def get_announcements_asserts_for_notarized_payments(not_payments, puzzle_hash=OFFER_MOD_HASH):
    _, conditions_dict, __ = conditions_dict_for_solution(
        OFFER_MOD,
        not_payments,
        INFINITE_COST
    )

    announcement_asserts = []
    for cwa in conditions_dict.get(ConditionOpcode.CREATE_PUZZLE_ANNOUNCEMENT, []): #cwa = condition with args
        announcement_asserts.append([
            ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT,
            std_hash(puzzle_hash + cwa.vars[0])
        ])

    return announcement_asserts


async def respond_to_deposit_liquidity_offer(
    pair_launcher_id,
    current_pair_coin,
    creation_spend,
    token_tail_hash,
    pair_liquidity,
    pair_xch_reserve,
    pair_token_reserve,
    offer_str,
    last_xch_reserve_coin,
    last_token_reserve_coin,
    last_token_reserve_lineage_proof, # coin_parent_coin_info, inner_puzzle_hash, amount
    return_address = DEFAULT_RETURN_ADDRESS
):
    # 1. Detect ephemeral coins (those created by the offer that we're allowed to use)
    offer = Offer.from_bech32(offer_str)
    offer_spend_bundle = offer.to_spend_bundle()
    offer_coin_spends = offer_spend_bundle.coin_spends

    eph_xch_coin = None
    eph_token_coin = None

    eph_token_coin_creation_spend = None # needed when spending eph_token_coin since it's a CAT
    announcement_asserts = [] # assert everything when the liquidity cat is minted

    ephemeral_token_coin_puzzle = construct_cat_puzzle(CAT_MOD, token_tail_hash, OFFER_MOD)
    ephemeral_token_coin_puzzle_hash = ephemeral_token_coin_puzzle.get_tree_hash()

    cs_from_initial_offer = [] # all valid coin spends (i.e., not 'hints' for offered assets or coins)
    
    for coin_spend in offer_coin_spends:
        if coin_spend.coin.parent_coin_info == b"\x00" * 32: # 'hint' for offer requested coin (liquidity CAT)
            continue
        
        cs_from_initial_offer.append(coin_spend)
        _, conditions_dict, __ = conditions_dict_for_solution(
            coin_spend.puzzle_reveal,
            coin_spend.solution,
            INFINITE_COST
        )

        # is this spend creating an ephemeral coin?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN, []):
            puzzle_hash = cwa.vars[0]
            amount = SExp.to(cwa.vars[1]).as_int()

            if puzzle_hash == OFFER_MOD_HASH:
                eph_xch_coin = Coin(coin_spend.coin.name(), puzzle_hash, amount)
            
            if puzzle_hash == ephemeral_token_coin_puzzle_hash:
                eph_token_coin = Coin(coin_spend.coin.name(), puzzle_hash, amount)
                eph_token_coin_creation_spend = coin_spend

        # is there any announcement that we'll need to assert?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, []): #cwa = condition with args
            announcement_asserts.append([
                ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT,
                std_hash(coin_spend.coin.name() + cwa.vars[0])
            ])

    # 2. Math stuff
    new_liquidity_token_amount = 0
    for k, v in offer.get_requested_amounts().items():
        new_liquidity_token_amount = v

    deposited_token_amount = new_liquidity_token_amount
    if pair_token_reserve != 0:
        deposited_token_amount = eph_token_coin.amount * pair_liquidity // pair_token_reserve
    
    deposited_xch_amount = eph_xch_coin.amount - new_liquidity_token_amount
    if pair_xch_reserve != 0:
        deposited_xch_amount = deposited_token_amount * pair_xch_reserve // pair_token_reserve

    if deposited_xch_amount > eph_xch_coin.amount or deposited_token_amount > eph_token_coin.amount:
        print(f"Uh... to few XCH/tokens :(")
        return None

    # 3. spend the token ephemeral coin to create the token reserve coin
    p2_singleton_puzzle = pay_to_singleton_flashloan_puzzle(pair_launcher_id)
    p2_singleton_puzzle_cat =  construct_cat_puzzle(CAT_MOD, token_tail_hash, p2_singleton_puzzle)
    
    eph_token_coin_notarized_payments = []
    eph_token_coin_notarized_payments.append(
        Program.to([
            current_pair_coin.name(),
            [p2_singleton_puzzle.get_tree_hash(), deposited_token_amount + pair_token_reserve]
        ])
    )

    # send extra tokens to return address
    if eph_token_coin.amount > deposited_token_amount:
        not_payment = Program.to([
            current_pair_coin.name(),
            [decode_puzzle_hash(return_address), eph_token_coin.amount - deposited_token_amount]
        ])
        eph_token_coin_notarized_payments.append(not_payment)
        for ann_assert in get_announcements_asserts_for_notarized_payments([not_payment], eph_token_coin.puzzle_hash):
            announcement_asserts.append(ann_assert)

    eph_token_coin_inner_solution = Program.to(eph_token_coin_notarized_payments)
    
    spendable_cats_for_token_reserve = []
    spendable_cats_for_token_reserve.append(
        SpendableCAT(
            eph_token_coin,
            token_tail_hash,
            OFFER_MOD,
            eph_token_coin_inner_solution,
            lineage_proof=LineageProof(
                eph_token_coin_creation_spend.coin.parent_coin_info,
                get_innerpuzzle_from_puzzle(eph_token_coin_creation_spend.puzzle_reveal).get_tree_hash(),
                eph_token_coin_creation_spend.coin.amount
            )
        )
    )

    pair_singleton_inner_puzzle = get_pair_inner_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    if last_token_reserve_coin is not None:
        spendable_cats_for_token_reserve.append(
            SpendableCAT(
                last_token_reserve_coin,
                token_tail_hash,
                p2_singleton_puzzle,
                solution_for_p2_singleton_flashloan(
                    last_token_reserve_coin, pair_singleton_inner_puzzle.get_tree_hash()
                ),
                lineage_proof=LineageProof(
                    last_token_reserve_lineage_proof[0],
                    last_token_reserve_lineage_proof[1],
                    last_token_reserve_lineage_proof[2]
                )
            )
        )

    token_reserve_creation_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD, spendable_cats_for_token_reserve
    )
    token_reserve_creation_spends = token_reserve_creation_spend_bundle.coin_spends
    
    # 4. spend the xch ephemeral coin
    liquidity_cat_tail = pair_liquidity_tail_puzzle(pair_launcher_id)
    liquidity_cat_tail_hash = liquidity_cat_tail.get_tree_hash()

    liquidity_cat_mint_coin_tail_solution = Program.to([pair_singleton_inner_puzzle.get_tree_hash()])
    # output everything from solution
    # this is usually not safe to use, but in this case we don't really care since we are accepting an offer
    # that is asking for liquidity tokens - it's kind of circular; if we don't get out tokens, 
    # the transaction doesn't go through because a puzzle announcemet from the parents of eph coins would fail
    liquidity_cat_mint_coin_inner_puzzle = Program.from_bytes(b"\x01")
    liquidity_cat_mint_coin_inner_puzzle_hash = liquidity_cat_mint_coin_inner_puzzle.get_tree_hash()
    liquidity_cat_mint_coin_puzzle = construct_cat_puzzle(
        CAT_MOD, liquidity_cat_tail_hash, liquidity_cat_mint_coin_inner_puzzle
    )
    liquidity_cat_mint_coin_puzzle_hash = liquidity_cat_mint_coin_puzzle.get_tree_hash()

    eph_xch_coin_settlement_things = [
        Program.to([
            current_pair_coin.name(),
            [p2_singleton_puzzle.get_tree_hash(), pair_xch_reserve + deposited_xch_amount]
        ]),
        Program.to([
            current_pair_coin.name(),
            [liquidity_cat_mint_coin_puzzle_hash, new_liquidity_token_amount]
        ])
    ]
    # send extra XCH to return address
    if eph_xch_coin.amount > deposited_xch_amount + new_liquidity_token_amount:
        not_payment = Program.to([
            current_pair_coin.name(),
            [decode_puzzle_hash(return_address), eph_xch_coin.amount - deposited_xch_amount - new_liquidity_token_amount]
        ])
        eph_xch_coin_settlement_things.append(not_payment)

        for ann_assert in get_announcements_asserts_for_notarized_payments([not_payment]):
            announcement_asserts.append(ann_assert)

    eph_xch_coin_solution = Program.to(eph_xch_coin_settlement_things)
    eph_xch_coin_spend = CoinSpend(eph_xch_coin, OFFER_MOD, eph_xch_coin_solution)

    # 5. Re-create the pair singleton (spend it)
    pair_singleton_puzzle = get_pair_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    pair_singleton_inner_solution = Program.to([
        Program.to([
            current_pair_coin.name(),
            b"\x00" * 32 if last_xch_reserve_coin is None else last_xch_reserve_coin.name(),
            b"\x00" * 32 if last_token_reserve_coin is None else last_token_reserve_coin.name(),
        ]),
        0,
        Program.to([
            deposited_token_amount,
            liquidity_cat_mint_coin_inner_puzzle_hash,
            eph_xch_coin.name(), # parent of liquidity cat mint coin
            deposited_xch_amount
        ])
    ])
    lineage_proof = lineage_proof_for_coinsol(creation_spend)
    pair_singleton_solution = solution_for_singleton(
        lineage_proof, current_pair_coin.amount, pair_singleton_inner_solution
    )
    pair_singleton_spend = CoinSpend(current_pair_coin, pair_singleton_puzzle, pair_singleton_solution)

    # 6. Spend liquidity cat mint coin
    liquidity_cat_mint_coin = Coin(
        eph_xch_coin.name(),
        liquidity_cat_mint_coin_puzzle_hash,
        new_liquidity_token_amount
    )

    output_conditions = announcement_asserts
    output_conditions.append([ConditionOpcode.CREATE_COIN, OFFER_MOD_HASH, new_liquidity_token_amount])

    liquidity_cat_mint_coin_tail_puzzle = pair_liquidity_tail_puzzle(pair_launcher_id)
    output_conditions.append([
        ConditionOpcode.CREATE_COIN,
        0,
        -113,
        liquidity_cat_mint_coin_tail_puzzle,
        liquidity_cat_mint_coin_tail_solution
    ])

    # also assert the XCH ephemeral coin announcement
    # specifically, for the payment made to mint liquidity tokens
    eph_xch_settlement_announcement = eph_xch_coin_settlement_things[-1].get_tree_hash()
    output_conditions.append([
        ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT,
        std_hash(eph_xch_coin.puzzle_hash + eph_xch_settlement_announcement)
    ])

    liquidity_cat_mint_coin_solution = Program.to([
        output_conditions,
        [],
        liquidity_cat_mint_coin.name(),
        [liquidity_cat_mint_coin.parent_coin_info, liquidity_cat_mint_coin.puzzle_hash, new_liquidity_token_amount],
        [liquidity_cat_mint_coin.parent_coin_info, liquidity_cat_mint_coin_inner_puzzle_hash, new_liquidity_token_amount],
        [],
        []
    ])
    liquidity_cat_mint_coin_spend = CoinSpend(
        liquidity_cat_mint_coin,
        liquidity_cat_mint_coin_puzzle,
        liquidity_cat_mint_coin_solution
    )

    # 7. Ephemeral liquidity cat
    ephemeral_liquidity_cat_coin_puzzle = construct_cat_puzzle(CAT_MOD, liquidity_cat_tail_hash, OFFER_MOD)
    ephemeral_liquidity_cat_coin_puzzle_hash = ephemeral_liquidity_cat_coin_puzzle.get_tree_hash()

    ephemeral_liquidity_cat = Coin(
        liquidity_cat_mint_coin.name(),
        ephemeral_liquidity_cat_coin_puzzle_hash,
        new_liquidity_token_amount
    )

    notarized_payment = offer.get_requested_payments()[liquidity_cat_tail_hash][0]
    nonce = notarized_payment.nonce
    memos = notarized_payment.memos
    ephemeral_liquidity_cat_inner_solution = Program.to([
        Program.to([
            nonce,
            [memos[0], new_liquidity_token_amount, memos]
        ])
    ])
    ephemeral_liquidity_cat_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                ephemeral_liquidity_cat,
                liquidity_cat_tail_hash,
                OFFER_MOD,
                ephemeral_liquidity_cat_inner_solution,
                lineage_proof=LineageProof(
                    liquidity_cat_mint_coin.parent_coin_info,
                    liquidity_cat_mint_coin_inner_puzzle_hash,
                    new_liquidity_token_amount
                )
            )
        ],
    )
    ephemeral_liquidity_cat_spend = ephemeral_liquidity_cat_spend_bundle.coin_spends[0]

    # 8. Right now, the tx is valid, but the fee is negative - spend the last (previous) xch reserve coin and we're done!
    last_xch_reserve_spend_maybe = []

    if last_xch_reserve_coin != None:
        last_xch_reserve_coin_puzzle = p2_singleton_puzzle
        last_xch_reserve_coin_solution = solution_for_p2_singleton_flashloan(
            last_xch_reserve_coin, pair_singleton_inner_puzzle.get_tree_hash()
        )
        last_xch_reserve_spend_maybe.append(
            CoinSpend(last_xch_reserve_coin, last_xch_reserve_coin_puzzle, last_xch_reserve_coin_solution)
        )

    sb = SpendBundle(
        [
           eph_xch_coin_spend,
           pair_singleton_spend,
           liquidity_cat_mint_coin_spend,
           ephemeral_liquidity_cat_spend
        ] + token_reserve_creation_spends + cs_from_initial_offer + last_xch_reserve_spend_maybe,
        offer_spend_bundle.aggregated_signature
    )

    return sb

async def respond_to_remove_liquidity_offer(
    pair_launcher_id,
    current_pair_coin,
    creation_spend,
    token_tail_hash,
    pair_liquidity,
    pair_xch_reserve,
    pair_token_reserve,
    offer_str,
    last_xch_reserve_coin,
    last_token_reserve_coin,
    last_token_reserve_lineage_proof, # coin_parent_coin_info, inner_puzzle_hash, amount
    return_address = DEFAULT_RETURN_ADDRESS
):
    # 1. detect offered ephemeral coin (ephemeral liquidity coin, created the offer so we can use it)
    offer = Offer.from_bech32(offer_str)
    offer_spend_bundle = offer.to_spend_bundle()
    offer_coin_spends = offer_spend_bundle.coin_spends

    eph_liquidity_coin = None

    eph_liquidity_coin_creation_spend = None # needed when spending eph_liquidity_coin since it's a CAT
    announcement_asserts = [] # assert everything when the old  XCH reserve is spent

    liquidity_cat_tail = pair_liquidity_tail_puzzle(pair_launcher_id)
    liquidity_cat_tail_hash = liquidity_cat_tail.get_tree_hash()
    eph_liquidity_coin_puzzle = construct_cat_puzzle(CAT_MOD, liquidity_cat_tail_hash, OFFER_MOD)
    eph_liquidity_coin_puzzle_hash = eph_liquidity_coin_puzzle.get_tree_hash()

    cs_from_initial_offer = [] # all valid coin spends (i.e., not 'hints' for offered assets or coins)

    for coin_spend in offer_coin_spends:
        if coin_spend.coin.parent_coin_info == b"\x00" * 32: # 'hint' for offer requested coin
            continue
        
        cs_from_initial_offer.append(coin_spend)
        _, conditions_dict, __ = conditions_dict_for_solution(
            coin_spend.puzzle_reveal,
            coin_spend.solution,
            INFINITE_COST
        )

        # is this spend creating an ephemeral coin?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN, []):
            puzzle_hash = cwa.vars[0]
            amount = SExp.to(cwa.vars[1]).as_int()

            if puzzle_hash == eph_liquidity_coin_puzzle_hash:
                eph_liquidity_coin = Coin(coin_spend.coin.name(), puzzle_hash, amount)
                eph_liquidity_coin_creation_spend = coin_spend

        # is there any announcement that we'll need to assert?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, []): #cwa = condition with args
            announcement_asserts.append([
                ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT,
                std_hash(coin_spend.coin.name() + cwa.vars[0])
            ])

    # 2. math stuff
    burned_liquidity_amount = eph_liquidity_coin.amount

    removed_token_amount = pair_token_reserve * burned_liquidity_amount // pair_liquidity
    removed_xch_amount = pair_xch_reserve * burned_liquidity_amount // pair_liquidity

    new_token_reserve_amount = last_token_reserve_coin.amount - removed_token_amount
    new_xch_reserve_amount = last_xch_reserve_coin.amount - removed_xch_amount

    # 3. spend ephemeral liquidity coin
    pair_singleton_puzzle = get_pair_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    pair_singleton_puzzle_hash = pair_singleton_puzzle.get_tree_hash()
    pair_singleton_inner_puzzle = get_pair_inner_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    pair_singleton_inner_puzzle_hash = pair_singleton_inner_puzzle.get_tree_hash()

    liquidity_cat_tail_puzzle = pair_liquidity_tail_puzzle(pair_launcher_id)
    liquidity_cat_tail_hash = liquidity_cat_tail_puzzle.get_tree_hash()
    liquidity_cat_burn_coin_tail_solution = Program.to([pair_singleton_inner_puzzle_hash])
    liquidity_burn_coin_inner_puzzle = Program.to((
        1,
        [[ConditionOpcode.CREATE_COIN, 0, -113, liquidity_cat_tail_puzzle, liquidity_cat_burn_coin_tail_solution]]
    ))
    liquidity_burn_coin_inner_puzzle_hash = liquidity_burn_coin_inner_puzzle.get_tree_hash()

    create_burn_coin_notarized_payment = Program.to([
        current_pair_coin.name(),
        [liquidity_burn_coin_inner_puzzle_hash, burned_liquidity_amount]
    ])
    offer_liquidity_cat_puzzle = construct_cat_puzzle(CAT_MOD, liquidity_cat_tail_hash, OFFER_MOD)
    announcement_asserts.append([
        ConditionOpcode.ASSERT_PUZZLE_ANNOUNCEMENT,
        std_hash(offer_liquidity_cat_puzzle.get_tree_hash() + create_burn_coin_notarized_payment.get_tree_hash())
    ])

    eph_liquidity_coin_inner_solution = Program.to([
        create_burn_coin_notarized_payment
    ])
    eph_liquidity_coin_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                eph_liquidity_coin,
                liquidity_cat_tail_hash,
                OFFER_MOD,
                eph_liquidity_coin_inner_solution,
                lineage_proof=LineageProof(
                    eph_liquidity_coin_creation_spend.coin.parent_coin_info,
                    get_innerpuzzle_from_puzzle(eph_liquidity_coin_creation_spend.puzzle_reveal).get_tree_hash(),
                    eph_liquidity_coin_creation_spend.coin.amount
                )
            )
        ]
    )
    eph_liquidity_coin_spend = eph_liquidity_coin_spend_bundle.coin_spends[0]

    # 4. spend liquidity burn coin
    liquidity_burn_coin_puzzle = construct_cat_puzzle(CAT_MOD, liquidity_cat_tail_hash, liquidity_burn_coin_inner_puzzle)
    liquidity_burn_coin = Coin(
        eph_liquidity_coin.name(),
        liquidity_burn_coin_puzzle.get_tree_hash(),
        burned_liquidity_amount
    )

    liquidity_burn_coin_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                liquidity_burn_coin,
                liquidity_cat_tail_hash,
                liquidity_burn_coin_inner_puzzle,
                Program.to([]),
                lineage_proof=LineageProof(
                    eph_liquidity_coin_spend.coin.parent_coin_info,
                    OFFER_MOD_HASH,
                    eph_liquidity_coin_spend.coin.amount
                ),
                limitations_program_reveal=liquidity_cat_tail_puzzle,
                limitations_solution=liquidity_cat_burn_coin_tail_solution,
                extra_delta=-burned_liquidity_amount,
            )
        ]
    )

    liquidity_burn_coin_spend = liquidity_burn_coin_spend_bundle.coin_spends[0]

    # 5. re-create the pair singleton (spend it)
    pair_singleton_puzzle = get_pair_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    pair_singleton_inner_solution = Program.to([
        Program.to([
            current_pair_coin.name(),
            last_xch_reserve_coin.name(),
            last_token_reserve_coin.name(),
        ]),
        1,
        Program.to([
            burned_liquidity_amount,
            liquidity_burn_coin_inner_puzzle_hash,
            liquidity_burn_coin.parent_coin_info
        ])
    ])
    lineage_proof = lineage_proof_for_coinsol(creation_spend)
    pair_singleton_solution = solution_for_singleton(
        lineage_proof, current_pair_coin.amount, pair_singleton_inner_solution
    )
    pair_singleton_spend = CoinSpend(current_pair_coin, pair_singleton_puzzle, pair_singleton_solution)

    # 6. spend token reserve
    p2_singleton_puzzle = pay_to_singleton_flashloan_puzzle(pair_launcher_id)
    p2_singleton_puzzle_hash = p2_singleton_puzzle.get_tree_hash()

    last_token_reserve_coin_extra_conditions = [
        [
            ConditionOpcode.CREATE_COIN,
            OFFER_MOD_HASH,
            last_token_reserve_coin.amount
        ]
    ]

    last_token_reserve_coin_inner_solution = solution_for_p2_singleton_flashloan(
        last_token_reserve_coin,
        pair_singleton_inner_puzzle.get_tree_hash(),
        extra_conditions=last_token_reserve_coin_extra_conditions
    )
    last_token_reserve_coin_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                last_token_reserve_coin,
                token_tail_hash,
                p2_singleton_puzzle,
                last_token_reserve_coin_inner_solution,
                lineage_proof=LineageProof(
                    last_token_reserve_lineage_proof[0],
                    last_token_reserve_lineage_proof[1],
                    last_token_reserve_lineage_proof[2]
                )
            )
        ]
    )
    last_token_reserve_coin_spend = last_token_reserve_coin_spend_bundle.coin_spends[0]

    # 7. spend ephemeral token coin to create new token reserve, resp. to offer
    eph_token_coin = Coin(
        last_token_reserve_coin.name(),
        construct_cat_puzzle(CAT_MOD, token_tail_hash, OFFER_MOD).get_tree_hash(),
        last_token_reserve_coin.amount
    )
    notarized_payments = offer.get_requested_payments()
    token_notarized_payment = notarized_payments[token_tail_hash][0]

    eph_token_coin_notarized_payments = []
    eph_token_coin_notarized_payments.append([
        token_notarized_payment.nonce,
        [token_notarized_payment.memos[0], removed_token_amount, token_notarized_payment.memos]
    ])
    if new_token_reserve_amount > 0:
        eph_token_coin_notarized_payments.append([
            current_pair_coin.name(),
            [p2_singleton_puzzle_hash, new_token_reserve_amount]
        ])
    if last_token_reserve_coin.amount > removed_token_amount + new_token_reserve_amount:
        not_payment = [
            current_pair_coin.name(),
            [decode_puzzle_hash(return_address), token_reserve_coin.amount - removed_token_amount - new_token_reserve_amount]
        ]
        eph_token_coin_notarized_payments.append(not_payment)

        for ann_assert in get_announcements_asserts_for_notarized_payments([not_payment], eph_token_coin.puzzle_hash):
            announcement_asserts.append(ann_assert)

    eph_token_coin_inner_solution = Program.to(eph_token_coin_notarized_payments)
    eph_token_coin_spend_bundle = unsigned_spend_bundle_for_spendable_cats(
        CAT_MOD,
        [
            SpendableCAT(
                eph_token_coin,
                token_tail_hash,
                OFFER_MOD,
                eph_token_coin_inner_solution,
                lineage_proof=LineageProof(
                    last_token_reserve_coin.parent_coin_info,
                    p2_singleton_puzzle_hash,
                    last_token_reserve_coin.amount
                )
            )
        ]
    )
    eph_token_coin_spend = eph_token_coin_spend_bundle.coin_spends[0]

    # 8. Spend XCH reserve
    xch_eph_coin_extra_payment = None
    if last_xch_reserve_coin.amount > removed_xch_amount + new_xch_reserve_amount:
        xch_eph_coin_extra_payment = [
            current_pair_coin.name(),
            [decode_puzzle_hash(return_address), xch_reserve_coin.amount - removed_xch_amount - new_xch_reserve_amount]
        ]

        for ann_assert in get_announcements_asserts_for_notarized_payments([xch_eph_coin_extra_payment]):
            announcement_asserts.append(ann_assert)

    last_xch_reserve_coin_extra_conditions = [
        [
            ConditionOpcode.CREATE_COIN,
            OFFER_MOD_HASH,
            last_xch_reserve_coin.amount
        ]
    ] + announcement_asserts

    last_xch_reserve_coin_solution = solution_for_p2_singleton_flashloan(
        last_xch_reserve_coin,
        pair_singleton_inner_puzzle.get_tree_hash(),
        extra_conditions=last_xch_reserve_coin_extra_conditions
    )
    last_xch_reserve_coin_spend = CoinSpend(
        last_xch_reserve_coin,
        p2_singleton_puzzle,
        last_xch_reserve_coin_solution
    )

    # 9. spend ephemeral XCH coin to create new XCH reserve, respond to offer
    eph_xch_coin = Coin(
        last_xch_reserve_coin.name(),
        OFFER_MOD_HASH,
        last_xch_reserve_coin.amount
    )
    xch_notarized_payment = notarized_payments.get(None)[0]

    eph_xch_coin_notarized_payments = []
    eph_xch_coin_notarized_payments.append(
        [
            xch_notarized_payment.nonce,
            [
                xch_notarized_payment.puzzle_hash,
                removed_xch_amount + burned_liquidity_amount,
                xch_notarized_payment.memos
            ]
        ]
    )
    if new_xch_reserve_amount > 0:
        eph_xch_coin_notarized_payments.append([
            current_pair_coin.name(),
            [p2_singleton_puzzle_hash, new_xch_reserve_amount]
        ])
    if xch_eph_coin_extra_payment is not None:
        eph_xch_coin_notarized_payments.append(xch_eph_coin_extra_payment)

    eph_xch_coin_solution = Program.to(eph_xch_coin_notarized_payments)
    eph_xch_coin_spend = CoinSpend(eph_xch_coin, OFFER_MOD, eph_xch_coin_solution)

    sb = SpendBundle(
        cs_from_initial_offer + [
           eph_liquidity_coin_spend,
           liquidity_burn_coin_spend,
           pair_singleton_spend,
           last_token_reserve_coin_spend,
           eph_token_coin_spend,
           last_xch_reserve_coin_spend,
           eph_xch_coin_spend
        ],
        offer_spend_bundle.aggregated_signature
    )

    return sb

async def respond_to_swap_offer(
    pair_launcher_id,
    current_pair_coin,
    creation_spend,
    token_tail_hash,
    pair_liquidity,
    pair_xch_reserve,
    pair_token_reserve,
    offer_str,
    last_xch_reserve_coin,
    last_token_reserve_coin,
    last_token_reserve_lineage_proof, # coin_parent_coin_info, inner_puzzle_hash, amount
    return_address = DEFAULT_RETURN_ADDRESS
):
    coin_spends = [] # all spends that will get included in the returned spend bundle

    # 1. detect offered ephemeral coin (XCH or token)
    offer = Offer.from_bech32(offer_str)
    offer_spend_bundle = offer.to_spend_bundle()
    offer_coin_spends = offer_spend_bundle.coin_spends

    eph_coin = None
    eph_coin_is_cat = False # true if token is offered, false if XCH is offered
    eph_coin_creation_spend = None # needed when spending eph_liquidity_coin since it's a CAT

    announcement_asserts = [] # assert everything when the old  XCH reserve is spent
    
    eph_token_coin_puzzle = construct_cat_puzzle(CAT_MOD, token_tail_hash, OFFER_MOD)
    eph_token_coin_puzzle_hash = eph_token_coin_puzzle.get_tree_hash()

    asked_for_amount = 0
    for k, v in offer.get_requested_amounts().items():
        asked_for_amount = v

    for coin_spend in offer_coin_spends:
        if coin_spend.coin.parent_coin_info == b"\x00" * 32: # 'hint' for offer requested coin
            continue
        
        coin_spends.append(coin_spend)
        _, conditions_dict, __ = conditions_dict_for_solution(
            coin_spend.puzzle_reveal,
            coin_spend.solution,
            INFINITE_COST
        )

        # is this spend creating an ephemeral coin?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN, []):
            puzzle_hash = cwa.vars[0]
            amount = SExp.to(cwa.vars[1]).as_int()

            if puzzle_hash in [OFFER_MOD_HASH, eph_token_coin_puzzle_hash]:
                eph_coin = Coin(coin_spend.coin.name(), puzzle_hash, amount)
                eph_coin_creation_spend = coin_spend
                eph_coin_is_cat = puzzle_hash == eph_token_coin_puzzle_hash

        # is there any announcement that we'll need to assert?
        for cwa in conditions_dict.get(ConditionOpcode.CREATE_COIN_ANNOUNCEMENT, []): #cwa = condition with args
            announcement_asserts.append([
                ConditionOpcode.ASSERT_COIN_ANNOUNCEMENT,
                std_hash(coin_spend.coin.name() + cwa.vars[0])
            ])

    # 2. math stuff
    
    new_xch_reserve_amount = pair_xch_reserve
    new_token_reserve_amount = pair_token_reserve

    if eph_coin_is_cat: # token offered, so swap is token -> XCH
        token_amount = eph_coin.amount
        new_xch_reserve_amount -= 993 * token_amount * pair_xch_reserve // (1000 * pair_token_reserve + 993 * token_amount)
        new_token_reserve_amount += token_amount
    else:
        xch_amount = eph_coin.amount
        new_token_reserve_amount -= 993 * xch_amount * pair_token_reserve // (1000 * pair_xch_reserve + 993 * xch_amount)
        new_xch_reserve_amount += xch_amount

    # 3. spend singleton
    pair_singleton_puzzle = get_pair_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )
    pair_singleton_inner_puzzle = get_pair_inner_puzzle(
        pair_launcher_id,
        token_tail_hash,
        pair_liquidity,
        pair_xch_reserve,
        pair_token_reserve
    )

    pair_singleton_inner_solution = Program.to([
        Program.to([
            current_pair_coin.name(),
            last_xch_reserve_coin.name(),
            last_token_reserve_coin.name(),
        ]),
        3 if eph_coin_is_cat else 2,
        Program.to([
            eph_coin.amount
        ])
    ])
    lineage_proof = lineage_proof_for_coinsol(creation_spend)
    pair_singleton_solution = solution_for_singleton(
        lineage_proof, current_pair_coin.amount, pair_singleton_inner_solution
    )

    pair_singleton_spend = CoinSpend(current_pair_coin, pair_singleton_puzzle, pair_singleton_solution)
    coin_spends.append(pair_singleton_spend)

    # 4. spend token reserve
    p2_singleton_puzzle = pay_to_singleton_flashloan_puzzle(pair_launcher_id)
    p2_singleton_puzzle_hash = p2_singleton_puzzle.get_tree_hash()

    last_token_reserve_coin_extra_conditions = [
        [
            ConditionOpcode.CREATE_COIN,
            OFFER_MOD_HASH,
            new_token_reserve_amount if eph_coin_is_cat else pair_token_reserve
        ]
    ]
    total_token_amount = last_token_reserve_coin.amount
    if eph_coin_is_cat:
        total_token_amount += eph_coin.amount
    else:
        total_token_amount -= total_token_amount

    if total_token_amount > new_token_reserve_amount:
        print(total_token_amount, new_token_reserve_amount)
        last_token_reserve_coin_extra_conditions.append([
            ConditionOpcode.CREATE_COIN,
            decode_puzzle_hash(return_address),
            total_token_amount - new_token_reserve_amount
        ])

    last_token_reserve_coin_inner_solution = solution_for_p2_singleton_flashloan(
        last_token_reserve_coin,
        pair_singleton_inner_puzzle.get_tree_hash(),
        extra_conditions=last_token_reserve_coin_extra_conditions
    )
    reserve_spendable_cats = [
        SpendableCAT(
            last_token_reserve_coin,
            token_tail_hash,
            p2_singleton_puzzle,
            last_token_reserve_coin_inner_solution,
            lineage_proof=LineageProof(
                last_token_reserve_lineage_proof[0],
                last_token_reserve_lineage_proof[1],
                last_token_reserve_lineage_proof[2]
            )
        )
    ]

    if eph_coin_is_cat:
        reserve_spendable_cats.append(
            SpendableCAT(
                eph_coin,
                token_tail_hash,
                OFFER_MOD,
                Program.to([]),
                lineage_proof=LineageProof(
                    eph_coin_creation_spend.coin.parent_coin_info,
                    get_innerpuzzle_from_puzzle(eph_coin_creation_spend.puzzle_reveal).get_tree_hash(),
                    eph_coin_creation_spend.coin.amount
                )
            )
        )

    for cs in unsigned_spend_bundle_for_spendable_cats(CAT_MOD, reserve_spendable_cats).coin_spends:
        coin_spends.append(cs)

    # 5. Spend intermediary token reserve coin
    intermediary_token_reserve_coin_amount = new_token_reserve_amount if eph_coin_is_cat else pair_token_reserve
    intermediary_token_reserve_coin = Coin(
        last_token_reserve_coin.name(),
        construct_cat_puzzle(CAT_MOD, token_tail_hash, OFFER_MOD).get_tree_hash(),
        intermediary_token_reserve_coin_amount
    )

    p2_singleton_puzzle = pay_to_singleton_flashloan_puzzle(pair_launcher_id)
    p2_singleton_puzzle_hash = p2_singleton_puzzle.get_tree_hash()
    p2_singleton_cat_puzzle = construct_cat_puzzle(CAT_MOD, token_tail_hash, p2_singleton_puzzle)
    p2_singleton_cat_puzzle_hash = p2_singleton_cat_puzzle.get_tree_hash()

    intermediary_token_reserve_notarized_payments = [
        [
            current_pair_coin.name(),
            [p2_singleton_puzzle_hash, new_token_reserve_amount]
        ]
    ]
    if not eph_coin_is_cat:
        notarized_payment = offer.get_requested_payments()[token_tail_hash][0]
        intermediary_token_reserve_notarized_payments.append(
            [
                notarized_payment.nonce,
                [notarized_payment.memos[0], pair_token_reserve - new_token_reserve_amount, notarized_payment.memos]
            ]
        )

    intermediary_token_reserve_coin_inner_solution = Program.to(intermediary_token_reserve_notarized_payments)

    intermediary_token_spend_bundle = unsigned_spend_bundle_for_spendable_cats(CAT_MOD, [
        SpendableCAT(
            intermediary_token_reserve_coin,
            token_tail_hash,
            OFFER_MOD,
            intermediary_token_reserve_coin_inner_solution,
            lineage_proof=LineageProof(
                last_token_reserve_coin.parent_coin_info,
                p2_singleton_puzzle_hash,
                last_token_reserve_coin.amount
            )
        )
    ])

    coin_spends.append(intermediary_token_spend_bundle.coin_spends[0])

    # 6. spend eph coin if it is xch
    if not eph_coin_is_cat:
        # just destroy the XCH; they'll be used to create the new reserve
        coin_spends.append(CoinSpend(eph_coin, OFFER_MOD, Program.to([])))

    # 7. Spend last xch reserve to create intermediary coin
    last_xch_reserve_coin_extra_conditions = [
        [
            ConditionOpcode.CREATE_COIN,
            OFFER_MOD_HASH,
            new_xch_reserve_amount
        ]
    ] + announcement_asserts

    total_xch_amount = last_xch_reserve_coin.amount
    if not eph_coin_is_cat:
        total_xch_amount += eph_coin.amount
    else:
        total_xch_amount -= asked_for_amount

    if total_xch_amount > new_xch_reserve_amount:
        last_token_reserve_coin_extra_conditions.append([
            ConditionOpcode.CREATE_COIN,
            decode_puzzle_hash(return_address),
            total_xch_amount - new_xch_reserve_amount
        ])

    last_xch_reserve_coin_solution = solution_for_p2_singleton_flashloan(
        last_xch_reserve_coin,
        pair_singleton_inner_puzzle.get_tree_hash(),
        extra_conditions=last_xch_reserve_coin_extra_conditions
    )

    last_xch_reserve_coin_spend = CoinSpend(
        last_xch_reserve_coin,
        p2_singleton_puzzle,
        last_xch_reserve_coin_solution
    )
    coin_spends.append(last_xch_reserve_coin_spend)

    # 8. Spend intermediary xch reserve coin
    intermediary_xch_reserve_coin = Coin(
        last_xch_reserve_coin.name(),
        OFFER_MOD_HASH,
        new_xch_reserve_amount
    )

    intermediary_xch_reserve_coin_notarized_payments = [
        [
            current_pair_coin.name(),
            [p2_singleton_puzzle_hash, new_xch_reserve_amount]
        ]
    ]
    if eph_coin_is_cat:
        notarized_payment = offer.get_requested_payments().get(None)[0]
        intermediary_xch_reserve_coin_notarized_payments.append(
            [
                notarized_payment.nonce,
                [notarized_payment.puzzle_hash, pair_xch_reserve - new_xch_reserve_amount, notarized_payment.memos]
            ]
        )
    intermediary_xch_reserve_coin_solution = Program.to(intermediary_xch_reserve_coin_notarized_payments)

    intermediary_token_reserve_coin_spend = CoinSpend(
        intermediary_xch_reserve_coin,
        OFFER_MOD,
        intermediary_xch_reserve_coin_solution
    )
    coin_spends.append(intermediary_token_reserve_coin_spend)

    return SpendBundle(
        coin_spends, offer_spend_bundle.aggregated_signature
    )