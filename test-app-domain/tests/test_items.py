"""Tests for Item CRUD operations."""


def test_create_item(client):
    """POST /api/items creates an item."""
    response = client.post("/api/items", json={
        "name": "Test Item",
        "description": "A test item",
        "quantity": 5,
    })
    assert response.status_code == 201
    data = response.get_json()
    assert data["name"] == "Test Item"
    assert data["quantity"] == 5
    assert "id" in data


def test_list_items(client):
    """GET /api/items returns all items."""
    # Create two items
    client.post("/api/items", json={"name": "Item 1", "quantity": 1})
    client.post("/api/items", json={"name": "Item 2", "quantity": 2})

    response = client.get("/api/items")
    assert response.status_code == 200
    data = response.get_json()
    assert data["total"] >= 2


def test_get_item(client):
    """GET /api/items/:id returns the item."""
    create_response = client.post("/api/items", json={"name": "Fetch Me"})
    item_id = create_response.get_json()["id"]

    response = client.get(f"/api/items/{item_id}")
    assert response.status_code == 200
    assert response.get_json()["name"] == "Fetch Me"


def test_get_item_not_found(client):
    """GET /api/items/:id returns 404 for nonexistent item."""
    response = client.get("/api/items/99999")
    assert response.status_code == 404


def test_update_item(client):
    """PUT /api/items/:id updates the item."""
    create_response = client.post("/api/items", json={"name": "Old Name"})
    item_id = create_response.get_json()["id"]

    response = client.put(f"/api/items/{item_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.get_json()["name"] == "New Name"


def test_delete_item(client):
    """DELETE /api/items/:id removes the item."""
    create_response = client.post("/api/items", json={"name": "Delete Me"})
    item_id = create_response.get_json()["id"]

    response = client.delete(f"/api/items/{item_id}")
    assert response.status_code == 204

    get_response = client.get(f"/api/items/{item_id}")
    assert get_response.status_code == 404
