import pytest

from model_test.model import validate_model


@pytest.fixture
def model():
    def state(identifier, description):
        return {
            "id": identifier,
            "name": identifier,
            "properties": {"business": {"description": description, "rules": ["The outcome is visible."]}},
        }

    def edge(identifier, source, target, **business):
        return {
            "id": identifier,
            "name": identifier,
            "sourceVertexId": source,
            "targetVertexId": target,
            "properties": {"business": {"intent": identifier, **business}},
        }

    return validate_model(
        {
            "models": [
                {
                    "id": "capacity",
                    "name": "Capacity reservation",
                    "startElementId": "form",
                    "properties": {
                        "business": {
                            "version": 3,
                            "purpose": "Reserve capacity",
                            "rules": ["Invalid capacity is rejected."],
                            "data sets": {
                                "Reservation": {
                                    "Places": {
                                        "description": "How many people attend",
                                        "type": "whole number",
                                        "required": True,
                                        "minimum": 1,
                                        "maximum": 4,
                                        "example": 2,
                                    },
                                }
                            },
                        }
                    },
                    "vertices": [
                        state("form", "Enter reservation details"),
                        state("accepted", "Reservation accepted"),
                        state("rejected", "Reservation rejected with an explanation"),
                    ],
                    "edges": [
                        edge(
                            "submit",
                            "form",
                            "accepted",
                            **{"data set": "Reservation", "rejected at": "rejected"},
                        ),
                        edge(
                            "submit_invalid",
                            "form",
                            "rejected",
                            **{
                                "data set": "Reservation",
                                "accepted at": "accepted",
                                "rejected at": "rejected",
                                "example": {"Places": 0},
                            },
                        ),
                        edge("another", "accepted", "form"),
                        edge("correct", "rejected", "form"),
                    ],
                }
            ]
        }
    )
