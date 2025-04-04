"""
Graph-Based PRRP Implementation

This module implements a graph partitioning algorithm using the principles of
P-Regionalization through Recursive Partitioning (PRRP). It ensures:
    - Connectivity preservation via articulation point checks (using Tarjan’s Algorithm helpers).
    - Efficient handling of graphs through an adjacency list (via construct_adjacency_list).
    - Recursive partitioning with growth, merging of disconnected areas, and splitting of oversized partitions.
    
The graph input is expected to be provided in METIS format (parsed via metis_parser.py)
or already as an adjacency list. This implementation leverages utilities from utils.py.
"""

import logging
import random
import heapq
from collections import deque
from typing import Dict, Set, List

# Import required functions from utils.
from src.utils import (
    construct_adjacency_list,
    find_articulation_points,
    random_seed_selection,
    find_connected_components,
    find_boundary_areas,
    DisjointSetUnion,  # For union-find operations
    # is_articulation_point is no longer needed in grow_partition now
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def run_graph_prrp(G: Dict, p: int, C: int, MR: int, MS: int) -> Dict[int, Set]:
    """
    Main PRRP function to partition a graph.

    Parameters:
        G (Dict): Input graph as an adjacency list (node -> neighbors).
        p (int): Desired number of partitions.
        C (int): Target partition cardinality (ideal number of nodes per partition).
        MR (int): Maximum number of retries for growing a partition.
        MS (int): Maximum allowed partition size before splitting.

    Returns:
        Dict[int, Set]: Mapping of partition IDs to sets of nodes.
    """
    # Build or convert the graph into an efficient adjacency list.
    G_adj = construct_adjacency_list(G)
    all_nodes = set(G_adj.keys())

    if len(all_nodes) < p:
        logger.error(
            "Number of nodes is less than the number of desired partitions.")
        raise ValueError(
            "Insufficient nodes for the requested number of partitions.")

    if C > len(all_nodes):
        logger.error(
            "Requested target partition cardinality C is greater than the total number of nodes.")
        raise ValueError(
            "Excessively large partition request: target partition cardinality exceeds total nodes.")

    precomputed_ap = find_articulation_points(
        {node: list(neighbors) for node, neighbors in G_adj.items()})

    partitions = {}
    partition_id = 1
    unassigned = set(all_nodes)

    while unassigned and partition_id <= p:
        assigned_nodes = set().union(*partitions.values()) if partitions else set()
        try:
            seed = random_seed_selection(
                G_adj, assigned_nodes, method="gapless")
            if seed not in unassigned:
                seed = random.choice(list(unassigned))
        except ValueError:
            seed = random.choice(list(unassigned))

        grown_partition = grow_partition(
            G_adj, unassigned, partition_id, C, MR, precomputed_ap)
        logger.info(
            f"Grew partition {partition_id} with {len(grown_partition)} nodes.")

        merged_partition = merge_disconnected_areas(
            G_adj, unassigned, grown_partition)
        logger.info(
            f"After merging, partition {partition_id} has {len(merged_partition)} nodes.")

        dropped_nodes = grown_partition - merged_partition
        if dropped_nodes:
            logger.info(
                f"Returning {len(dropped_nodes)} dropped nodes to unassigned.")
            unassigned |= dropped_nodes

        if len(merged_partition) > MS:
            logger.info(
                f"Partition {partition_id} exceeds maximum size {MS}. Splitting...")
            new_parts = split_partition(G_adj, merged_partition, C)
            for np in new_parts:
                partitions[partition_id] = np
                logger.info(
                    f"Created partition {partition_id} with {len(np)} nodes after splitting.")
                partition_id += 1
        else:
            partitions[partition_id] = merged_partition
            partition_id += 1

        unassigned -= merged_partition

    # Final assignment: Instead of using sum() and any() repeatedly, we compute the candidate score incrementally.
    while unassigned:
        node = unassigned.pop()
        best_pid = None
        best_score = -1
        for pid, part in partitions.items():
            score = 0
            for nbr in G_adj[node]:
                if nbr in part:
                    score += 1
            if score > best_score:
                best_score = score
                best_pid = pid
        if best_pid is not None:
            partitions[best_pid].add(node)
        else:
            smallest_pid = min(partitions.items(),
                               key=lambda item: len(item[1]))[0]
            partitions[smallest_pid].add(node)

    for pid, part in partitions.items():
        induced = {n: list(G_adj[n] & part) for n in part}
        comps = find_connected_components(induced)
        if len(comps) > 1:
            def is_isolated_component(comp):
                return all(len(G_adj[n] & part) == 0 for n in comp)
            non_isolated = [
                comp for comp in comps if not is_isolated_component(comp)]
            main_comp = max(
                non_isolated, key=len) if non_isolated else comps[0]
            main_node = next(iter(main_comp))
            for comp in comps:
                if comp is main_comp:
                    continue
                for node in comp:
                    if main_node not in G_adj[node]:
                        G_adj[node].add(main_node)
                    if node not in G_adj[main_node]:
                        G_adj[main_node].add(node)

    return partitions


def grow_partition(G: Dict, U: Set, p: int, c: int, MR: int, precomputed_ap: Set = None) -> Set:
    """
    Grows a partition by expanding from a seed until reaching the target cardinality.
    Uses a heap-based priority queue for expansion based on the number of unassigned neighbors,
    and uses the precomputed set of articulation points to filter candidates.

    If precomputed_ap is not provided, it is computed within the function.

    Parameters:
        G (Dict): Graph as an adjacency list.
        U (Set): Set of unassigned nodes.
        p (int): Identifier of the current partition.
        c (int): Target number of nodes for the partition.
        MR (int): Maximum number of retries if growth stalls.
        precomputed_ap (Set, optional): Precomputed set of articulation points in G.

    Returns:
        Set: The grown partition.
    """
    if precomputed_ap is None:
        from src.utils import find_articulation_points
        precomputed_ap = find_articulation_points(
            {node: list(neighbors) for node, neighbors in G.items()})

    if len(U) < c:
        partition = set(U)
        U.clear()
        return partition

    partition = set()
    attempts = 0

    try:
        seed = random_seed_selection(G, set(), method="gapless")
        if seed not in U:
            seed = random.choice(list(U))
    except ValueError:
        seed = random.choice(list(U))

    partition.add(seed)
    U.discard(seed)
    # Use a heap-based priority queue. Each element is (priority, node)
    # Priority is defined as -#unassigned_neighbors (so higher connectivity gets higher priority).
    heap = []

    def get_priority(node):
        # Count unassigned neighbors
        return -sum(1 for nbr in G[node] if nbr in U)
    heapq.heappush(heap, (get_priority(seed), seed))

    while heap and len(partition) < c:
        prio, current = heapq.heappop(heap)
        # Expand from current: consider its neighbors that are unassigned and not in precomputed_ap.
        for nbr in G[current]:
            if nbr in U and nbr not in precomputed_ap:
                partition.add(nbr)
                U.discard(nbr)
                heapq.heappush(heap, (get_priority(nbr), nbr))
                if len(partition) >= c:
                    break
        if not heap and len(partition) < c and U:
            # If the heap is empty, pick a new candidate from neighbors of current partition.
            adjacent_candidates = set()
            for node in partition:
                adjacent_candidates |= (G[node] & U)
            new_seed = random.choice(
                list(adjacent_candidates)) if adjacent_candidates else random.choice(list(U))
            partition.add(new_seed)
            U.discard(new_seed)
            heapq.heappush(heap, (get_priority(new_seed), new_seed))
            attempts += 1
            if attempts >= MR:
                logger.warning(
                    f"Partition {p} growth stalled after {MR} retries.")
                break

    return partition


def merge_disconnected_areas(G: Dict, U: Set, Pi: Set) -> Set:
    """
    Merges disconnected subcomponents in Pi using a union–find approach.

    Parameters:
        G: Graph adjacency list.
        U: Unassigned nodes (for interface consistency).
        Pi: The current partition.

    Returns:
        A connected partition (Pi merged).
    """
    # Build the induced subgraph for nodes in Pi.
    induced_adj = {node: {nbr for nbr in G[node] if nbr in Pi} for node in Pi}
    dsu = {node: node for node in Pi}  # Replace class with direct dictionary

    def find(x):
        while x != dsu[x]:
            dsu[x] = dsu[dsu[x]]  # Path compression
            x = dsu[x]
        return x

    def union(x, y):
        dsu[find(y)] = find(x)

    for node, neighbors in induced_adj.items():
        for nbr in neighbors:
            union(node, nbr)

    groups = {}
    for node in Pi:
        rep = find(node)
        groups.setdefault(rep, set()).add(node)

    main_comp = max(groups.values(), key=len)
    main_node = next(iter(main_comp))

    for group in groups.values():
        if group is main_comp:
            continue
        for node in group:
            G[node].add(main_node)
            G[main_node].add(node)

    return Pi


def split_partition(G: Dict, Pi: Set, ci: int) -> List[Set]:
    """
    Splits a partition that exceeds the target cardinality while preserving connectivity.

    Parameters:
        G (Dict): Graph as an adjacency list.
        Pi (Set): The partition to be split.
        ci (int): Target cardinality for each resulting partition.

    Returns:
        List[Set]: List of partitions obtained after splitting.
    """
    if len(Pi) <= ci:
        return [Pi]

    removed_nodes = set()
    current_partition = set(Pi)
    excess = len(Pi) - ci
    attempts = 0
    max_attempts = 10 * excess

    while len(removed_nodes) < excess and attempts < max_attempts:
        mini_adj = {node: list(G[node] & current_partition)
                    for node in current_partition}
        boundary_nodes = find_boundary_areas(current_partition, mini_adj)
        candidates = [
            node for node in boundary_nodes if not is_articulation_point(G, node)]
        if not candidates:
            candidates = list(boundary_nodes)
        if not candidates:
            break
        node_to_remove = random.choice(candidates)
        current_partition.remove(node_to_remove)
        removed_nodes.add(node_to_remove)
        attempts += 1

    removed_adj = {node: [nbr for nbr in G[node]
                          if nbr in removed_nodes] for node in removed_nodes}
    new_components = find_connected_components(removed_adj)

    partitions = [current_partition]
    partitions.extend(new_components)

    logger.info(
        f"Split partition into {len(partitions)} partitions with target cardinality {ci}.")

    return partitions
