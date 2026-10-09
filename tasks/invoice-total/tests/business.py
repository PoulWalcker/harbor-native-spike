from acceptance.contracts import WorkflowObservation, equal, require


def _produced(observation: WorkflowObservation, operation: str) -> dict:
    matches = [sid for sid, event in observation.events.items() if event["operation"] == operation]
    require(len(matches) == 1, "Missing or duplicate operation")
    sid = matches[0]
    return observation.states[sid]["steps"][sid]


def invoice_total(
    inputs: dict,
    observation: WorkflowObservation,
    mode: str = "stub",
    *,
    case: dict | None = None,
) -> None:
    output = observation.final["output"]
    require(
        all(event["status"] == "completed" for event in observation.events.values()),
        "Unexpected skipped operation",
    )
    invoices = inputs["invoices"]
    expected = {
        "total_minor": sum(x["amount_minor"] for x in invoices),
        "currency": invoices[0]["currency"],
        "invoice_count": len(invoices),
    }
    equal(output, expected, "Wrong invoice result")
    equal(
        _produced(observation, "invoices.validate"),
        {"invoices": invoices},
        "Invoice validation changed/lost invoices",
    )
    equal(
        _produced(observation, "invoices.sum"),
        {
            "amount_minor": expected["total_minor"],
            "currency": expected["currency"],
            "count": expected["invoice_count"],
        },
        "Wrong intermediate sum",
    )
    equal(
        _produced(observation, "invoices.report"),
        expected,
        "Wrong report operation output",
    )
