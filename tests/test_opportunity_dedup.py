from autonomous_agent.models import Opportunity
from autonomous_agent.opportunity_dedup import deduplicate_opportunities


def test_deduplicate_keeps_highest_score_and_longest_description() -> None:
    items = [
        Opportunity(
            title="Audit service",
            description="short",
            score=60,
            next_step="Package the audit",
        ),
        Opportunity(
            title=" audit-service ",
            description="a more informative description",
            score=85,
            next_step="package the audit",
        ),
    ]

    result = deduplicate_opportunities(items)

    assert len(result) == 1
    assert result[0].score == 85
    assert result[0].description == "a more informative description"


def test_deduplicate_preserves_distinct_order_and_does_not_mutate_input() -> None:
    first = Opportunity(title="One", description="a", score=10, next_step="Do one")
    second = Opportunity(title="Two", description="b", score=20, next_step="Do two")

    result = deduplicate_opportunities([first, second])

    assert result == [first, second]
