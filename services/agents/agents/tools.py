
import math
from dataclasses import dataclass
from typing import Any, Callable

from services.agents.data.data import CsvData


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    properties: dict
    required: list[str]
    function: Callable[..., Any]

    def bedrock_spec(self):
        return {
            "toolSpec": {
                "name": self.name,
                "description": self.description,
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": self.properties,
                        "required": self.required,
                    }
                },
            }
        }


class ToolRegistry:
    def __init__(self, tools: list[AgentTool]):
        self._tools = {tool.name: tool for tool in tools}

        if len(self._tools) != len(tools):
            raise ValueError("Duplicate tool names are not allowed.")

    @property
    def specs(self):
        return [tool.bedrock_spec() for tool in self._tools.values()]

    @property
    def names(self):
        return set(self._tools)

    def execute(self, name: str, arguments: dict):
        tool = self._tools.get(name)

        if tool is None:
            raise ValueError(f"Tool is not permitted: {name}")

        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object.")

        missing = set(tool.required) - set(arguments)
        unexpected = set(arguments) - set(tool.properties)

        if missing:
            raise ValueError(f"Missing arguments: {sorted(missing)}")

        if unexpected:
            raise ValueError(f"Unexpected arguments: {sorted(unexpected)}")

        return tool.function(**arguments)


def build_tool_registries(data: CsvData):
    """Create separate, least-privilege tool registries."""

    def search_drug_catalogue(query: str):
        return data.search_drugs(query)

    def get_site_stock(din: str):
        return data.stock(din)

    def get_open_orders(din: str):
        return data.orders(din)

    def get_substitutions(din: str):
        return data.substitutions(din)

    def get_spare_units(
        din: str,
        site_id: str,
        keep_days: float = 14,
    ):
        if keep_days < 0:
            raise ValueError("keep_days cannot be negative.")

        rows = data.stock(din)
        site = next(
            (row for row in rows if row["site_id"] == site_id),
            None,
        )

        if site is None:
            raise ValueError(
                f"No inventory found for DIN {din} at {site_id}."
            )

        spare = math.floor(
            site["on_hand"] - site["daily_use"] * keep_days
        )

        return {
            "din": din,
            "site_id": site_id,
            "on_hand": site["on_hand"],
            "daily_use": site["daily_use"],
            "keep_days": keep_days,
            "spare_units": max(0, spare),
        }

    string = lambda description: {
        "type": "string",
        "description": description,
    }

    number = lambda description: {
        "type": "number",
        "description": description,
    }

    search = AgentTool(
        name="search_drug_catalogue",
        description="Search the drug catalogue by drug name.",
        properties={
            "query": string("Drug name or search phrase.")
        },
        required=["query"],
        function=search_drug_catalogue,
    )

    stock = AgentTool(
        name="get_site_stock",
        description="Retrieve inventory and daily usage for a drug DIN across sites.",
        properties={
            "din": string("Drug Identification Number.")
        },
        required=["din"],
        function=get_site_stock,
    )

    orders = AgentTool(
        name="get_open_orders",
        description="Retrieve open purchase orders for a drug DIN.",
        properties={
            "din": string("Drug Identification Number.")
        },
        required=["din"],
        function=get_open_orders,
    )

    substitutions = AgentTool(
        name="get_approved_substitutions",
        description="Retrieve existing approved substitution records for a drug DIN. This tool does not approve new substitutions.",
        properties={
            "din": string("Drug Identification Number.")
        },
        required=["din"],
        function=get_substitutions,
    )

    spare = AgentTool(
        name="get_spare_units",
        description="Calculate stock above a site's required reserve.",
        properties={
            "din": string("Drug Identification Number."),
            "site_id": string("Inventory site identifier."),
            "keep_days": number("Minimum days of stock to retain."),
        },
        required=["din", "site_id"],
        function=get_spare_units,
    )

    # Health Canada reference data, only when the data source has it (the
    # database). CsvData has none, so CSV mode gets exactly the tools it had.
    reference = []

    if hasattr(data, "shortage_reports"):
        reference.append(AgentTool(
            name="get_shortage_reports",
            description=(
                "Retrieve Health Canada shortage reports for a drug DIN: status, "
                "Tier 3 flag, reason, start and estimated end dates. Official "
                "public data, read only."
            ),
            properties={"din": string("Drug Identification Number.")},
            required=["din"],
            function=lambda din: data.shortage_reports(din),
        ))

    if hasattr(data, "drug_product"):
        reference.append(AgentTool(
            name="get_drug_product",
            description=(
                "Retrieve the Health Canada Drug Product Database record for a "
                "DIN: brand, company, marketing status, ingredients, form and "
                "route. Returns null if the DIN is not in the database."
            ),
            properties={"din": string("Drug Identification Number.")},
            required=["din"],
            function=lambda din: data.drug_product(din),
        ))

    assessment_tools = ToolRegistry([
        search,
        stock,
        orders,
        *reference,
    ])

    planning_tools = ToolRegistry([
        stock,
        orders,
        substitutions,
        spare,
        *reference,
    ])

    return assessment_tools, planning_tools
