"""
Visualizations
==============

ASCII and text-based visualizations for benchmark results.

Note: For production use with actual graphics, use matplotlib/seaborn.
This module provides text-based visualizations that work in any terminal.
"""

import math
from typing import Dict, List, Optional, Tuple


class Visualizer:
    """
    Creates text-based visualizations of benchmark results.

    For actual graphical output, this class can be extended to use
    matplotlib, but the text versions work anywhere.
    """

    def __init__(self, width: int = 70):
        self.width = width

    # ==========================================================================
    # Bar Charts
    # ==========================================================================

    def bar_chart(
        self,
        data: Dict[str, float],
        title: str = "",
        max_bar_width: int = 40,
        show_values: bool = True,
        as_percentage: bool = True,
    ) -> str:
        """
        Create a horizontal bar chart.

        Args:
            data: Dictionary of label -> value
            title: Chart title
            max_bar_width: Maximum width of bars
            show_values: Show numeric values
            as_percentage: Display values as percentages
        """
        lines = []

        if title:
            lines.append(title)
            lines.append("=" * len(title))
            lines.append("")

        if not data:
            return "\n".join(lines) + "(No data)"

        max_value = max(data.values()) if data.values() else 1
        max_label_len = max(len(str(k)) for k in data)

        for label, value in data.items():
            bar_len = int((value / max_value) * max_bar_width) if max_value > 0 else 0
            bar = "█" * bar_len + "░" * (max_bar_width - bar_len)

            if show_values:
                if as_percentage:
                    value_str = f"{value:6.1%}"
                else:
                    value_str = f"{value:6.2f}"
                lines.append(f"{label:<{max_label_len}} │{bar}│ {value_str}")
            else:
                lines.append(f"{label:<{max_label_len}} │{bar}│")

        return "\n".join(lines)

    def grouped_bar_chart(
        self,
        data: Dict[str, Dict[str, float]],
        title: str = "",
        max_bar_width: int = 30,
    ) -> str:
        """
        Create a grouped horizontal bar chart.

        Args:
            data: Dictionary of group -> {category -> value}
            title: Chart title
        """
        lines = []

        if title:
            lines.append(title)
            lines.append("=" * len(title))
            lines.append("")

        if not data:
            return "\n".join(lines) + "(No data)"

        # Get all categories
        all_categories: set[str] = set()
        for group_data in data.values():
            all_categories.update(group_data.keys())
        categories = sorted(all_categories)

        # Find max value
        max_value: float = 0
        for group_data in data.values():
            max_value = max(max_value, max(group_data.values(), default=0))

        max_label_len = max(len(str(k)) for k in data)

        # Bar characters for different categories
        bar_chars = ["█", "▓", "▒", "░"]

        # Legend
        legend_parts = []
        for i, cat in enumerate(categories):
            char = bar_chars[i % len(bar_chars)]
            legend_parts.append(f"{char} {cat}")
        lines.append("Legend: " + "  ".join(legend_parts))
        lines.append("")

        for group, group_data in data.items():
            lines.append(f"{group}:")
            for i, cat in enumerate(categories):
                value = group_data.get(cat, 0)
                bar_len = (
                    int((value / max_value) * max_bar_width) if max_value > 0 else 0
                )
                char = bar_chars[i % len(bar_chars)]
                bar = char * bar_len
                lines.append(f"  {cat:<15} │{bar:<{max_bar_width}}│ {value:5.1%}")
            lines.append("")

        return "\n".join(lines)

    # ==========================================================================
    # Tables
    # ==========================================================================

    def table(
        self,
        headers: List[str],
        rows: List[List[str]],
        title: str = "",
        alignments: List[str] | None = None,
    ) -> str:
        """
        Create a formatted table.

        Args:
            headers: Column headers
            rows: List of rows, each a list of values
            title: Table title
            alignments: List of 'l', 'r', or 'c' for each column
        """
        lines = []

        if title:
            lines.append(title)
            lines.append("")

        if not headers:
            return "\n".join(lines) + "(No data)"

        # Calculate column widths
        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # Default alignments
        if not alignments:
            alignments = ["l"] * len(headers)

        def format_cell(value: str, width: int, align: str) -> str:
            if align == "r":
                return value.rjust(width)
            elif align == "c":
                return value.center(width)
            else:
                return value.ljust(width)

        # Header
        header_cells = [
            format_cell(h, col_widths[i], alignments[i]) for i, h in enumerate(headers)
        ]
        lines.append("│ " + " │ ".join(header_cells) + " │")

        # Separator
        sep_cells = ["─" * w for w in col_widths]
        lines.append("├─" + "─┼─".join(sep_cells) + "─┤")

        # Data rows
        for row in rows:
            row_cells = []
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    row_cells.append(
                        format_cell(str(cell), col_widths[i], alignments[i])
                    )
            lines.append("│ " + " │ ".join(row_cells) + " │")

        return "\n".join(lines)

    # ==========================================================================
    # Contingency Table
    # ==========================================================================

    def contingency_table(
        self,
        both_pass: int,
        a_only: int,
        b_only: int,
        both_fail: int,
        label_a: str = "A",
        label_b: str = "B",
        title: str = "Contingency Table",
    ) -> str:
        """Create a 2x2 contingency table visualization."""
        total = both_pass + a_only + b_only + both_fail

        lines = []
        lines.append(title)
        lines.append("=" * len(title))
        lines.append("")

        # Header
        lines.append(
            f"{'':>15} │ {label_b + ' Pass':>12} │ {label_b + ' Fail':>12} │ {'Total':>8}"
        )
        lines.append("─" * 15 + "─┼─" + "─" * 12 + "─┼─" + "─" * 12 + "─┼─" + "─" * 8)

        # Row A Pass
        a_pass_total = both_pass + a_only
        lines.append(
            f"{label_a + ' Pass':>15} │ {both_pass:>12} │ {a_only:>12} │ {a_pass_total:>8}"
        )

        # Row A Fail
        a_fail_total = b_only + both_fail
        lines.append(
            f"{label_a + ' Fail':>15} │ {b_only:>12} │ {both_fail:>12} │ {a_fail_total:>8}"
        )

        # Separator
        lines.append("─" * 15 + "─┼─" + "─" * 12 + "─┼─" + "─" * 12 + "─┼─" + "─" * 8)

        # Totals
        b_pass_total = both_pass + b_only
        b_fail_total = a_only + both_fail
        lines.append(
            f"{'Total':>15} │ {b_pass_total:>12} │ {b_fail_total:>12} │ {total:>8}"
        )

        # Percentages
        lines.append("")
        lines.append("Percentages:")
        if total > 0:
            lines.append(f"  Both pass: {both_pass / total:6.1%}")
            lines.append(f"  {label_a} only:  {a_only / total:6.1%}")
            lines.append(f"  {label_b} only:  {b_only / total:6.1%}")
            lines.append(f"  Both fail: {both_fail / total:6.1%}")

        return "\n".join(lines)

    # ==========================================================================
    # Line/Trend Charts
    # ==========================================================================

    def line_chart(
        self,
        data: Dict[str, List[Tuple[str, float]]],
        title: str = "",
        height: int = 15,
        width: int = 50,
    ) -> str:
        """
        Create an ASCII line chart.

        Args:
            data: Dictionary of series_name -> [(x_label, y_value), ...]
            title: Chart title
            height: Chart height in characters
            width: Chart width in characters
        """
        lines = []

        if title:
            lines.append(title)
            lines.append("=" * len(title))
            lines.append("")

        if not data:
            return "\n".join(lines) + "(No data)"

        # Find min/max values
        all_values: list[float] = []
        for series in data.values():
            all_values.extend(v for _, v in series)

        if not all_values:
            return "\n".join(lines) + "(No data)"

        min_val = min(all_values)
        max_val = max(all_values)

        if max_val == min_val:
            max_val = min_val + 1

        # Get all x labels
        x_labels = []
        for series in data.values():
            for label, _ in series:
                if label not in x_labels:
                    x_labels.append(label)

        # Create grid
        grid = [[" " for _ in range(width)] for _ in range(height)]

        # Plot series
        markers = ["●", "○", "◆", "◇", "■", "□"]
        for series_idx, (_series_name, points) in enumerate(data.items()):
            marker = markers[series_idx % len(markers)]

            for i, (_label, value) in enumerate(points):
                x = int(i / max(len(points) - 1, 1) * (width - 1))
                y = int((value - min_val) / (max_val - min_val) * (height - 1))
                y = height - 1 - y  # Flip y axis
                if 0 <= x < width and 0 <= y < height:
                    grid[y][x] = marker

        # Build chart
        for i, row in enumerate(grid):
            y_val = max_val - (i / (height - 1)) * (max_val - min_val)
            lines.append(f"{y_val:6.2f} │{''.join(row)}")

        # X axis
        lines.append("       └" + "─" * width)

        # X labels (simplified)
        if x_labels:
            lines.append(
                "        "
                + x_labels[0].ljust(width // 2)
                + x_labels[-1].rjust(width // 2)
            )

        # Legend
        lines.append("")
        legend_parts = []
        for i, name in enumerate(data.keys()):
            marker = markers[i % len(markers)]
            legend_parts.append(f"{marker} {name}")
        lines.append("Legend: " + "  ".join(legend_parts))

        return "\n".join(lines)

    # ==========================================================================
    # Summary Box
    # ==========================================================================

    def summary_box(self, title: str, stats: Dict[str, str], width: int = 50) -> str:
        """Create a summary statistics box."""
        lines = []

        # Top border
        lines.append("┌" + "─" * (width - 2) + "┐")

        # Title
        title_padded = f" {title} ".center(width - 2)
        lines.append("│" + title_padded + "│")
        lines.append("├" + "─" * (width - 2) + "┤")

        # Stats
        max_key_len = max(len(k) for k in stats) if stats else 10
        for key, value in stats.items():
            content = f" {key:<{max_key_len}} : {value}"
            content = content.ljust(width - 2)[: width - 2]
            lines.append("│" + content + "│")

        # Bottom border
        lines.append("└" + "─" * (width - 2) + "┘")

        return "\n".join(lines)

    # ==========================================================================
    # Comparison Diagram
    # ==========================================================================

    def comparison_diagram(
        self,
        value_a: float,
        value_b: float,
        label_a: str = "A",
        label_b: str = "B",
        title: str = "Comparison",
    ) -> str:
        """Create a visual comparison between two values."""
        lines = []

        lines.append(title)
        lines.append("=" * len(title))
        lines.append("")

        max_val = max(value_a, value_b, 0.001)
        bar_width = 30

        # A bar
        a_len = int(value_a / max_val * bar_width)
        a_bar = "█" * a_len + "░" * (bar_width - a_len)
        lines.append(f"{label_a:>10} │{a_bar}│ {value_a:.1%}")

        # B bar
        b_len = int(value_b / max_val * bar_width)
        b_bar = "█" * b_len + "░" * (bar_width - b_len)
        lines.append(f"{label_b:>10} │{b_bar}│ {value_b:.1%}")

        # Difference
        diff = value_a - value_b
        rel_diff = diff / value_b if value_b > 0 else float("inf")

        lines.append("")
        lines.append(f"Difference: {diff:+.1%} (relative: {rel_diff:+.1%})")

        if diff > 0:
            lines.append(f"→ {label_a} is better by {abs(diff):.1%}")
        elif diff < 0:
            lines.append(f"→ {label_b} is better by {abs(diff):.1%}")
        else:
            lines.append("→ No difference")

        return "\n".join(lines)


# Convenience function
def create_visualizer(width: int = 70) -> Visualizer:
    """Create a new Visualizer instance."""
    return Visualizer(width)
