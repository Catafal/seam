package com.example.service;

import java.util.List;
import java.util.Map;

// WHY: Sample Java fixture exercising all extraction targets for Phase 9 tests.

/**
 * Repository interface defining the data-access contract.
 * Every store implementation must satisfy this interface.
 */
public interface DataRepository {
    void save(String item);
    String load(String key);
}

/**
 * Status enum for entity lifecycle tracking.
 */
public enum EntityStatus {
    ACTIVE,
    INACTIVE,
    DELETED
}

/**
 * A simple Point record carrying x/y coordinates.
 */
public record GeoPoint(int x, int y) {}

/**
 * WHY: DataStore is the primary repository used by the service layer.
 * It wraps a simple in-memory map for demonstration purposes.
 */
// HACK: using Map instead of a real DB — replace before production
@SuppressWarnings("unchecked")
@Service
public class DataStore implements DataRepository {

    private final Map<String, String> store;

    /**
     * Constructs a new DataStore backed by the given map.
     *
     * @param store the backing map (must not be null)
     */
    public DataStore(Map<String, String> store) {
        this.store = store;
        // NOTE: init() must be called to warm up the cache
        init();
    }

    /**
     * Persists the item under its key.
     *
     * @param item the value to store
     */
    @Override
    public void save(String item) {
        persist(item);
    }

    /**
     * Retrieves an item by key, returning null if absent.
     */
    @Override
    public String load(String key) {
        return retrieve(key);
    }

    // Private helpers below — not part of the public API.

    private void init() {
        this.store.clear();
    }

    private void persist(String item) {
        this.store.put(item, item);
    }

    private String retrieve(String key) {
        return this.store.get(key);
    }
}
