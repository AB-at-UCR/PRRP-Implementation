"""
tests/test_spatial_prrp.py

Unit tests for the P-Regionalization through Recursive Partitioning (PRRP) Algorithm.
This test suite covers:
  - Gapless seed selection (get_gapless_seed)
  - Region growing (grow_region)
  - Region merging (merge_disconnected_areas)
  - Region splitting (split_region)
  - Full PRRP execution (run_prrp)
  - Parallel execution of PRRP (run_parallel_prrp)
  - Loading and processing of a real spatial dataset
  - Construction and PRRP execution on synthetic grid data

Each test ensures that spatial contiguity and cardinality constraints are maintained.
"""

import os
import unittest
import random
from copy import deepcopy
from typing import Dict, Set, List, Any, Tuple

# Import functions to be tested from the PRRP module.
from src.spatial_prrp import (
    get_gapless_seed,
    grow_region,
    merge_disconnected_areas,
    split_region,
    run_prrp,
    run_parallel_prrp
)
# Import utility functions.
from src.utils import find_connected_components, construct_adjacency_list
# Import the shapefile loader from the data loader module.
from src.prrp_data_loader import load_shapefile

# For generating synthetic geometries.
from shapely.geometry import box
import geopandas as gpd


# ==============================
# Helper Functions for Test Data
# ==============================
def generate_test_graph() -> Dict[int, Set[int]]:
    """
    Generates a small, manually designed adjacency list representing spatial areas.

    Returns:
        Dict[int, Set[int]]: A simple adjacency list for testing.
    """
    return {
        1: {2, 5},
        2: {1, 3, 6},
        3: {2, 4, 7},
        4: {3, 8},
        5: {1, 6, 9},
        6: {2, 5, 7, 10},
        7: {3, 6, 8, 11},
        8: {4, 7, 12},
        9: {5, 10},
        10: {6, 9, 11},
        11: {7, 10, 12},
        12: {8, 11}
    }


def generate_test_areas() -> List[Dict[str, Any]]:
    """
    Generates a synthetic list of spatial areas with dummy geometry (None).

    Returns:
        List[Dict[str, Any]]: A list of spatial area dictionaries.
    """
    return [{'id': i, 'geometry': None} for i in range(1, 13)]


def generate_grid_areas(rows: int, cols: int) -> List[Dict[str, Any]]:
    """
    Generates a grid of square polygons representing spatial areas.

    Each area is a square of unit length. The 'id' of the area is a sequential
    number starting at 1. The grid is arranged such that the cell at row i and column j
    has coordinates (j, i) to (j+1, i+1).

    Parameters:
        rows (int): Number of rows in the grid.
        cols (int): Number of columns in the grid.

    Returns:
        List[Dict[str, Any]]: A list of spatial area dictionaries with 'id' and 'geometry'.
    """
    areas = []
    for i in range(rows):
        for j in range(cols):
            area_id = i * cols + j + 1
            polygon = box(j, i, j+1, i+1)
            areas.append({'id': area_id, 'geometry': polygon})
    return areas


def generate_grid_test_data(rows: int, cols: int) -> Tuple[List[Dict[str, Any]], Dict[int, Set[int]]]:
    """
    Generates synthetic grid test data and computes the expected rook-adjacency list.

    The expected adjacency is computed based on 4-neighbor connectivity (top, bottom, left, right).

    Parameters:
        rows (int): Number of rows in the grid.
        cols (int): Number of columns in the grid.

    Returns:
        Tuple:
            - List[Dict[str, Any]]: The list of spatial areas with square geometries.
            - Dict[int, Set[int]]: The expected adjacency list mapping area ids to neighbor ids.
    """
    areas = generate_grid_areas(rows, cols)
    expected_adj_list = {}
    for i in range(rows):
        for j in range(cols):
            area_id = i * cols + j + 1
            neighbors = set()
            # Top neighbor.
            if i > 0:
                neighbors.add((i - 1) * cols + j + 1)
            # Bottom neighbor.
            if i < rows - 1:
                neighbors.add((i + 1) * cols + j + 1)
            # Left neighbor.
            if j > 0:
                neighbors.add(i * cols + (j - 1) + 1)
            # Right neighbor.
            if j < cols - 1:
                neighbors.add(i * cols + (j + 1) + 1)
            expected_adj_list[area_id] = neighbors
    return areas, expected_adj_list


# ==============================
# Test Suite
# ==============================
class TestSpatialPRRP(unittest.TestCase):

    def setUp(self):
        """
        Initializes test data before each test case.
        Creates a synthetic spatial graph and a dummy list of areas.
        """
        self.adj_list: Dict[int, Set[int]] = generate_test_graph()
        self.available_areas: Set[int] = set(self.adj_list.keys())
        self.areas: List[Dict[str, Any]] = generate_test_areas()
        # Set target cardinalities for three regions.
        self.cardinalities = [4, 4, 4]
        self.num_regions = len(self.cardinalities)

    # ==============================
    # 1. Test get_gapless_seed
    # ==============================
    def test_get_gapless_seed(self):
        """
        Tests that get_gapless_seed selects a valid seed.
        When assigned regions exist, the seed is preferred from their neighbors.
        Also tests that an empty available areas set raises ValueError.
        """
        assigned_regions = {1, 2, 3}  # Some areas are already assigned.
        seed = get_gapless_seed(
            self.adj_list, self.available_areas, assigned_regions)
        self.assertIn(seed, self.available_areas,
                      "Seed must be selected from available areas.")

        # Test with no assigned regions (first region case).
        seed2 = get_gapless_seed(self.adj_list, self.available_areas, set())
        self.assertIn(seed2, self.available_areas,
                      "With no assigned regions, seed should be from available areas.")

        # Edge Case: empty available_areas should raise a ValueError.
        with self.assertRaises(ValueError):
            get_gapless_seed(self.adj_list, set(), assigned_regions)

    # ==============================
    # 2. Test grow_region
    # ==============================
    def test_grow_region(self):
        """
        Tests that grow_region produces a region with the target cardinality and that
        the region is spatially contiguous.
        """
        target_size = 4
        available = deepcopy(self.available_areas)
        region = grow_region(self.adj_list, available,
                             target_cardinality=target_size)
        self.assertEqual(len(region), target_size,
                         "Region must match the target cardinality.")

        # Verify spatial contiguity: build a subgraph for the region.
        subgraph = {area: list(self.adj_list.get(
            area, set()) & region) for area in region}
        components = find_connected_components(subgraph)
        self.assertEqual(len(components), 1,
                         "Grown region should be spatially contiguous.")

    def test_grow_region_insufficient_areas(self):
        """
        Tests that growing a region when available areas are insufficient raises a ValueError.
        """
        available = {1, 2}  # Too few areas for a target of 5.
        with self.assertRaises(ValueError):
            grow_region(self.adj_list, available, target_cardinality=5)

    # ==============================
    # 3. Test merge_disconnected_areas
    # ==============================
    def test_merge_disconnected_areas(self):
        """
        Tests that merge_disconnected_areas correctly merges disconnected components into the region.
        In this test, a disconnected available area is simulated by modifying the available areas.
        The disconnected component (containing area 12) should be merged into the current region.
        """
        region = {1, 2, 3, 4}
        available = deepcopy(self.available_areas) - region

        # Simulate disconnection for area 12:
        # Remove areas 8 and 11 from available to isolate area 12.
        available.discard(8)
        available.discard(11)
        available.add(12)  # Ensure area 12 is in available

        merged_region = merge_disconnected_areas(
            self.adj_list, available, region)
        self.assertIn(12, merged_region,
                      "Disconnected area should be merged into the region.")

    # ==============================
    # 4. Test split_region
    # ==============================
    def test_split_region(self):
        """
        Tests that split_region correctly removes excess areas to meet the target cardinality
        while maintaining spatial contiguity.
        """
        # Define an oversized region.
        region = {1, 2, 3, 4, 5, 6}
        target_size = 4
        adjusted_region = split_region(
            region, target_cardinality=target_size, adj_list=self.adj_list)
        self.assertEqual(len(adjusted_region), target_size,
                         "After splitting, region should have target cardinality.")

        # Verify spatial contiguity.
        subgraph = {area: list(self.adj_list.get(
            area, set()) & adjusted_region) for area in adjusted_region}
        components = find_connected_components(subgraph)
        self.assertEqual(len(components), 1,
                         "Split region must remain contiguous.")

    # ==============================
    # 5. Test run_prrp (Full PRRP Execution)
    # ==============================
    def test_run_prrp(self):
        """
        Tests the full PRRP pipeline.
        Verifies that:
          - The correct number of regions are produced.
          - Each region meets its cardinality constraint.
          - The union of regions covers all areas.
        """
        regions = run_prrp(self.areas, self.num_regions, self.cardinalities)
        self.assertEqual(len(regions), self.num_regions,
                         "Should produce the correct number of regions.")

        for i, region in enumerate(regions):
            self.assertEqual(len(region), self.cardinalities[i],
                             f"Region {i+1} must meet its cardinality constraint.")

        # Check that all areas are assigned.
        all_region_areas = set().union(*regions)
        expected_areas = set(range(1, 13))
        self.assertEqual(all_region_areas, expected_areas,
                         "All areas should be assigned across regions.")

    # ==============================
    # 6. Test run_parallel_prrp (Parallel Execution)
    # ==============================
    def test_run_parallel_prrp(self):
        """
        Tests parallel execution of PRRP.
        Verifies that:
          - The number of solutions matches the requested count.
          - Each solution is valid (correct number of regions and areas assigned).
          - Solutions are statistically independent (not all identical).
        """
        solutions_count = 3
        parallel_solutions = run_parallel_prrp(
            self.areas, self.num_regions, self.cardinalities,
            solutions_count=solutions_count, num_threads=2
        )
        self.assertEqual(len(parallel_solutions), solutions_count,
                         "Number of parallel solutions should match requested count.")

        expected_areas = set(range(1, 13))
        for solution in parallel_solutions:
            self.assertEqual(len(solution), self.num_regions,
                             "Each solution must have the correct number of regions.")
            union_areas = set().union(*solution)
            self.assertEqual(union_areas, expected_areas,
                             "Each solution must assign all areas.")

        # Check for statistical independence: the solutions should not all be identical.
        unique_solutions = {frozenset(frozenset(region) for region in sol)
                            for sol in parallel_solutions}
        self.assertGreater(len(unique_solutions), 1,
                           "Parallel solutions should be statistically independent and not identical.")

    # ==============================
    # 7. Test run_prrp_invalid_cardinalities
    # ==============================
    def test_run_prrp_invalid_cardinalities(self):
        """
        Tests that run_prrp raises a ValueError when the number of regions
        does not match the length of the cardinalities list.
        """
        with self.assertRaises(ValueError):
            run_prrp(self.areas, num_regions=4, cardinalities=[
                     5, 5, 5])  # 4 != len([5,5,5])

    # ==============================
    # New Tests: Real Dataset Integration
    # ==============================
    def test_load_shapefile(self):
        """
        Tests that the shapefile is loaded correctly from the real dataset.
        Verifies that a non-empty list of areas is returned and that each area
        has the expected 'id' and 'geometry' keys.
        """
        shapefile_path = os.path.abspath(os.path.join(
            os.getcwd(), 'data/cb_2015_42_tract_500k/cb_2015_42_tract_500k.shp'))
        if not os.path.exists(shapefile_path):
            self.skipTest(f"Shapefile not found at {shapefile_path}")
        areas = load_shapefile(shapefile_path)
        self.assertIsNotNone(areas, "Loaded areas should not be None.")
        self.assertGreater(len(areas), 0, "Loaded areas should not be empty.")
        for area in areas:
            self.assertIn('id', area, "Each area must have an 'id' key.")
            self.assertIn('geometry', area,
                          "Each area must have a 'geometry' key.")
            self.assertIsNotNone(
                area['geometry'], "Area geometry should not be None.")

    def test_run_prrp_real_dataset(self):
        """
        Tests full PRRP execution using the real dataset.
        Verifies that the number of regions and area assignments are as expected.
        """
        shapefile_path = os.path.abspath(os.path.join(
            os.getcwd(), 'data/cb_2015_42_tract_500k/cb_2015_42_tract_500k.shp'))
        if not os.path.exists(shapefile_path):
            self.skipTest(f"Shapefile not found at {shapefile_path}")
        areas = load_shapefile(shapefile_path)
        total_areas = len(areas)
        # Create a scenario with 5 regions and random target sizes that sum to total_areas.
        num_regions = 5
        cardinalities = [random.randint(5, 15) for _ in range(num_regions - 1)]
        cardinalities.append(total_areas - sum(cardinalities))
        regions = run_prrp(areas, num_regions, cardinalities)
        self.assertEqual(len(regions), num_regions,
                         "Should produce the correct number of regions for real data.")
        all_assigned = set().union(*regions)
        expected_ids = {area['id'] for area in areas}
        self.assertEqual(all_assigned, expected_ids,
                         "All areas must be assigned in the real dataset.")

    def test_run_parallel_prrp_real_dataset(self):
        """
        Tests parallel PRRP execution on the real dataset.
        Verifies that parallel solutions are produced and are valid.
        """
        shapefile_path = os.path.abspath(os.path.join(
            os.getcwd(), 'data/cb_2015_42_tract_500k/cb_2015_42_tract_500k.shp'))
        if not os.path.exists(shapefile_path):
            self.skipTest(f"Shapefile not found at {shapefile_path}")
        areas = load_shapefile(shapefile_path)
        total_areas = len(areas)
        num_regions = 5
        cardinalities = [random.randint(5, 15) for _ in range(num_regions - 1)]
        cardinalities.append(total_areas - sum(cardinalities))
        solutions_count = 3
        parallel_solutions = run_parallel_prrp(
            areas, num_regions, cardinalities, solutions_count=solutions_count, num_threads=2)
        self.assertEqual(len(parallel_solutions), solutions_count,
                         "Parallel execution should produce the requested number of solutions.")
        expected_ids = {area['id'] for area in areas}
        for solution in parallel_solutions:
            self.assertEqual(len(solution), num_regions,
                             "Each solution must have the correct number of regions.")
            union_ids = set().union(*solution)
            self.assertEqual(union_ids, expected_ids,
                             "Each parallel solution must assign all areas.")

    # ==============================
    # New Tests: Synthetic Grid Data
    # ==============================
    def test_construct_adjacency_list_grid(self):
        """
        Tests that the construct_adjacency_list function creates the correct
        adjacency list for synthetic grid data.
        """
        rows, cols = 3, 4  # Create a 3x4 grid.
        areas, expected_adj_list = generate_grid_test_data(rows, cols)
        # Convert list of dicts to a GeoDataFrame.
        gdf = gpd.GeoDataFrame(areas)
        adj_list = construct_adjacency_list(gdf)
        # Convert neighbor lists to sets.
        adj_list = {k: set(v) for k, v in adj_list.items()}
        self.assertEqual(adj_list, expected_adj_list,
                         "Constructed adjacency list for grid does not match expected.")

    def test_run_prrp_grid_data(self):
        """
        Tests full PRRP execution on synthetic grid data.
        Verifies that:
          - The correct number of regions are produced.
          - Each region meets its cardinality constraint.
          - The union of regions covers all area IDs in the grid.
        """
        rows, cols = 4, 5  # Create a 4x5 grid (20 areas).
        areas, _ = generate_grid_test_data(rows, cols)
        total_areas = len(areas)
        num_regions = 4
        # Generate random cardinalities that sum to total_areas.
        cardinalities = [random.randint(3, 7) for _ in range(num_regions - 1)]
        cardinalities.append(total_areas - sum(cardinalities))
        regions = run_prrp(areas, num_regions, cardinalities)
        self.assertEqual(len(regions), num_regions,
                         "Should produce the correct number of regions for grid data.")
        all_assigned = set().union(*regions)
        expected_ids = {area['id'] for area in areas}
        self.assertEqual(all_assigned, expected_ids,
                         "All grid areas must be assigned.")

    def test_run_parallel_prrp_grid_data(self):
        """
        Tests parallel PRRP execution on synthetic grid data.
        Verifies that parallel solutions are produced and that each solution covers
        all areas in the grid.
        """
        rows, cols = 5, 5  # Create a 5x5 grid (25 areas).
        areas, _ = generate_grid_test_data(rows, cols)
        total_areas = len(areas)
        num_regions = 3
        cardinalities = [random.randint(5, 10) for _ in range(num_regions - 1)]
        cardinalities.append(total_areas - sum(cardinalities))
        solutions_count = 3
        parallel_solutions = run_parallel_prrp(
            areas, num_regions, cardinalities, solutions_count=solutions_count, num_threads=2)
        self.assertEqual(len(parallel_solutions), solutions_count,
                         "Parallel execution should produce the requested number of solutions.")
        expected_ids = {area['id'] for area in areas}
        for solution in parallel_solutions:
            self.assertEqual(len(solution), num_regions,
                             "Each solution must have the correct number of regions for grid data.")
            union_ids = set().union(*solution)
            self.assertEqual(union_ids, expected_ids,
                             "Each parallel solution must assign all grid areas.")


if __name__ == '__main__':
    unittest.main()
