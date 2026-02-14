"""Tests for Items CRUD API."""

import pytest
from flask.testing import FlaskClient


class TestItemsAPI:
    """Tests for the /api/items endpoints."""

    def test_list_items_empty(self, client: FlaskClient) -> None:
        """Test listing items when none exist."""
        response = client.get("/api/items")
        assert response.status_code == 200
        data = response.get_json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_create_item(self, client: FlaskClient) -> None:
        """Test creating a new item."""
        response = client.post(
            "/api/items",
            json={"name": "Test Item", "description": "A test item", "quantity": 5},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["name"] == "Test Item"
        assert data["description"] == "A test item"
        assert data["quantity"] == 5
        assert "id" in data

    def test_get_item(self, client: FlaskClient) -> None:
        """Test getting an item by ID."""
        # Create an item first
        create_resp = client.post(
            "/api/items",
            json={"name": "Get Me"},
        )
        item_id = create_resp.get_json()["id"]

        response = client.get(f"/api/items/{item_id}")
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "Get Me"
        assert data["id"] == item_id

    def test_get_item_not_found(self, client: FlaskClient) -> None:
        """Test getting a non-existent item."""
        response = client.get("/api/items/9999")
        assert response.status_code == 404

    def test_update_item(self, client: FlaskClient) -> None:
        """Test updating an item."""
        # Create an item first
        create_resp = client.post(
            "/api/items",
            json={"name": "Original", "quantity": 1},
        )
        item_id = create_resp.get_json()["id"]

        response = client.patch(
            f"/api/items/{item_id}",
            json={"name": "Updated", "quantity": 10},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["name"] == "Updated"
        assert data["quantity"] == 10

    def test_delete_item(self, client: FlaskClient) -> None:
        """Test deleting an item."""
        # Create an item first
        create_resp = client.post(
            "/api/items",
            json={"name": "Delete Me"},
        )
        item_id = create_resp.get_json()["id"]

        response = client.delete(f"/api/items/{item_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/items/{item_id}")
        assert get_resp.status_code == 404
