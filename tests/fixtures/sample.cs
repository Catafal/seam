// WHY: Sample C# fixture exercising all extraction targets for Phase 9 tests.
using System;
using System.Collections.Generic;

namespace SampleApp.Services
{
    /// <summary>
    /// Defines the contract for storage operations.
    /// </summary>
    public interface IDataRepository
    {
        void Save(string item);
        string Load(string key);
    }

    /// <summary>
    /// Lifecycle status values for tracked entities.
    /// </summary>
    public enum EntityStatus
    {
        Active,
        Inactive,
        Deleted
    }

    /// <summary>
    /// Lightweight type alias for callback notification.
    /// </summary>
    public delegate void NotifyCallback(string message);

    /// <summary>
    /// WHY: DataStore is the canonical repository implementation used by the service.
    /// Wraps a Dictionary for simplicity; swap for a real DB in production.
    /// </summary>
    // HACK: Dictionary used instead of real persistence — replace before go-live
    [Serializable]
    [DataContract]
    public class DataStore : IDataRepository
    {
        private readonly Dictionary<string, string> _store;

        /// <summary>
        /// Constructs a new DataStore.
        /// </summary>
        // NOTE: The backing dictionary is injected to allow test doubles.
        public DataStore(Dictionary<string, string> store)
        {
            _store = store;
            Initialize();
        }

        /// <summary>Persists the item using its value as key.</summary>
        public void Save(string item)
        {
            Persist(item);
        }

        /// <summary>Retrieves an item by key, or null if absent.</summary>
        public string Load(string key)
        {
            return Retrieve(key);
        }

        private void Initialize()
        {
            _store.Clear();
        }

        private void Persist(string item)
        {
            _store[item] = item;
        }

        private string Retrieve(string key)
        {
            return _store.ContainsKey(key) ? _store[key] : null;
        }
    }

    /// <summary>
    /// Read-only view over the repository.
    /// </summary>
    public struct DataView
    {
        public string Name { get; }

        public DataView(string name)
        {
            Name = name;
        }
    }
}
