use std::collections::HashMap;
use std::hash::{Hash, Hasher};

#[derive(Debug)]
struct BlockEntry {
    ref_count: u32,
    lru_prev: Option<u64>,
    lru_next: Option<u64>,
    in_lru: bool,
}

#[derive(Debug)]
pub struct PrefixCache {
    cache: HashMap<u64, BlockEntry>,
    lru_head: Option<u64>,
    lru_tail: Option<u64>,
}

impl PrefixCache {
    pub fn bytes_used(&self, block_size: u32, kv_bytes_per_token_per_device: u64) -> u64 {
        self.cache.len() as u64 * u64::from(block_size) * kv_bytes_per_token_per_device
    }

    pub fn longest_hit(&mut self, tokens: &[u32], block_size: u32) -> u32 {
        let mut hit_blocks = 0u32;
        let bs = block_size as usize;
        let mut parent_hash: u64 = 0;

        for chunk in tokens.chunks(bs) {
            if chunk.len() < bs {
                break;
            }
            let h = chain_hash(parent_hash, chunk);
            if self.cache.contains_key(&h) {
                self.touch(h);
                hit_blocks += 1;
                parent_hash = h;
            } else {
                break;
            }
        }

        hit_blocks * block_size
    }

    pub fn insert_prefix(&mut self, prefix: &[u32], block_size: u32) {
        let hashes = compute_block_hashes(prefix, block_size);
        for h in hashes {
            if self.cache.contains_key(&h) {
                self.touch(h);
            } else {
                let entry = BlockEntry {
                    ref_count: 0,
                    lru_prev: None,
                    lru_next: None,
                    in_lru: false,
                };
                self.cache.insert(h, entry);
                self.lru_push_back(h);
            }
        }
    }

    pub fn lock_prefix(&mut self, prefix: &[u32], block_size: u32) {
        let hashes = compute_block_hashes(prefix, block_size);
        for &h in &hashes {
            let should_remove = self
                .cache
                .get(&h)
                .map_or(false, |e| e.ref_count == 0 && e.in_lru);
            if should_remove {
                self.lru_remove(h);
            }
            if let Some(entry) = self.cache.get_mut(&h) {
                entry.ref_count += 1;
            }
        }
    }

    pub fn unlock_prefix(&mut self, prefix: &[u32], block_size: u32) {
        let hashes = compute_block_hashes(prefix, block_size);
        for &h in &hashes {
            if let Some(entry) = self.cache.get_mut(&h) {
                entry.ref_count = entry.ref_count.saturating_sub(1);
            }
            let should_push = self
                .cache
                .get(&h)
                .map_or(false, |e| e.ref_count == 0 && !e.in_lru);
            if should_push {
                self.lru_push_back(h);
            }
        }
    }

    pub fn evict_one_leaf(&mut self) -> bool {
        let Some(h) = self.lru_head else {
            return false;
        };
        self.lru_pop_front();
        self.cache.remove(&h);
        true
    }

    fn touch(&mut self, h: u64) {
        let Some(entry) = self.cache.get(&h) else {
            return;
        };
        if entry.in_lru {
            self.lru_remove(h);
            self.lru_push_back(h);
        }
    }

    fn lru_push_back(&mut self, h: u64) {
        let entry = self.cache.get_mut(&h).unwrap();
        entry.in_lru = true;
        entry.lru_prev = self.lru_tail;
        entry.lru_next = None;

        if let Some(old_tail) = self.lru_tail {
            self.cache.get_mut(&old_tail).unwrap().lru_next = Some(h);
        } else {
            self.lru_head = Some(h);
        }
        self.lru_tail = Some(h);
    }

    fn lru_remove(&mut self, h: u64) {
        let entry = self.cache.get_mut(&h).unwrap();
        let prev = entry.lru_prev.take();
        let next = entry.lru_next.take();
        entry.in_lru = false;

        match prev {
            Some(p) => self.cache.get_mut(&p).unwrap().lru_next = next,
            None => self.lru_head = next,
        }
        match next {
            Some(n) => self.cache.get_mut(&n).unwrap().lru_prev = prev,
            None => self.lru_tail = prev,
        }
    }

    fn lru_pop_front(&mut self) {
        if let Some(h) = self.lru_head {
            self.lru_remove(h);
        }
    }
}

impl Default for PrefixCache {
    fn default() -> Self {
        Self {
            cache: HashMap::new(),
            lru_head: None,
            lru_tail: None,
        }
    }
}

fn chain_hash(parent_hash: u64, chunk: &[u32]) -> u64 {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    parent_hash.hash(&mut hasher);
    chunk.hash(&mut hasher);
    hasher.finish()
}

fn compute_block_hashes(tokens: &[u32], block_size: u32) -> Vec<u64> {
    let bs = block_size as usize;
    let num_full_blocks = tokens.len() / bs;
    let mut hashes = Vec::with_capacity(num_full_blocks);
    let mut parent_hash: u64 = 0;
    for i in 0..num_full_blocks {
        let chunk = &tokens[i * bs..(i + 1) * bs];
        parent_hash = chain_hash(parent_hash, chunk);
        hashes.push(parent_hash);
    }
    hashes
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tokens(n: usize) -> Vec<u32> {
        (0..n as u32).collect()
    }

    #[test]
    fn empty_cache_returns_zero_hit() {
        let mut cache = PrefixCache::default();
        assert_eq!(cache.longest_hit(&tokens(32), 16), 0);
    }

    #[test]
    fn insert_and_hit_single_block() {
        let mut cache = PrefixCache::default();
        let toks = tokens(16);
        cache.insert_prefix(&toks, 16);
        assert_eq!(cache.longest_hit(&toks, 16), 16);
    }

    #[test]
    fn hit_returns_block_aligned_length() {
        let mut cache = PrefixCache::default();
        let toks = tokens(16);
        cache.insert_prefix(&toks, 16);
        assert_eq!(cache.longest_hit(&tokens(20), 16), 16);
    }

    #[test]
    fn chain_hash_validates_prefix() {
        let mut cache = PrefixCache::default();
        cache.insert_prefix(&tokens(16), 16);
        cache.insert_prefix(&tokens(32), 16);
        assert_eq!(cache.longest_hit(&tokens(32), 16), 32);

        let mut bad_prefix: Vec<u32> = vec![999; 16];
        bad_prefix.extend(16..32u32);
        assert_eq!(cache.longest_hit(&bad_prefix, 16), 0);
    }

    #[test]
    fn insert_idempotent() {
        let mut cache = PrefixCache::default();
        let toks = tokens(16);
        cache.insert_prefix(&toks, 16);
        cache.insert_prefix(&toks, 16);
        assert_eq!(cache.bytes_used(16, 1), 16);
    }

    #[test]
    fn lock_prevents_eviction() {
        let mut cache = PrefixCache::default();
        let toks = tokens(16);
        cache.insert_prefix(&toks, 16);
        cache.lock_prefix(&toks, 16);
        assert!(!cache.evict_one_leaf());
        cache.unlock_prefix(&toks, 16);
        assert!(cache.evict_one_leaf());
    }

    #[test]
    fn evict_removes_lru_first() {
        let mut cache = PrefixCache::default();
        let a = tokens(16);
        let b: Vec<u32> = (100..116).collect();

        cache.insert_prefix(&a, 16);
        cache.insert_prefix(&b, 16);

        assert!(cache.evict_one_leaf());
        assert_eq!(cache.longest_hit(&a, 16), 0);
        assert_eq!(cache.longest_hit(&b, 16), 16);
    }

    #[test]
    fn touch_on_hit_updates_lru_order() {
        let mut cache = PrefixCache::default();
        let a = tokens(16);
        let b: Vec<u32> = (100..116).collect();

        cache.insert_prefix(&a, 16);
        cache.insert_prefix(&b, 16);

        cache.longest_hit(&a, 16);

        assert!(cache.evict_one_leaf());
        assert_eq!(cache.longest_hit(&a, 16), 16);
        assert_eq!(cache.longest_hit(&b, 16), 0);
    }

    #[test]
    fn bytes_used_tracks_block_count() {
        let mut cache = PrefixCache::default();
        assert_eq!(cache.bytes_used(16, 100), 0);

        cache.insert_prefix(&tokens(16), 16);
        assert_eq!(cache.bytes_used(16, 100), 1600);

        cache.insert_prefix(&tokens(32), 16);
        assert_eq!(cache.bytes_used(16, 100), 3200);
    }

    #[test]
    fn evict_returns_false_when_empty() {
        let mut cache = PrefixCache::default();
        assert!(!cache.evict_one_leaf());
    }

    #[test]
    fn partial_block_ignored() {
        let mut cache = PrefixCache::default();
        cache.insert_prefix(&tokens(10), 16);
        assert_eq!(cache.bytes_used(16, 1), 0);
        assert_eq!(cache.longest_hit(&tokens(10), 16), 0);
    }

    #[test]
    fn lock_multi_block_prevents_parent_eviction() {
        let mut cache = PrefixCache::default();
        cache.insert_prefix(&tokens(16), 16);
        cache.insert_prefix(&tokens(32), 16);

        cache.lock_prefix(&tokens(32), 16);
        assert!(!cache.evict_one_leaf());

        cache.unlock_prefix(&tokens(32), 16);
        assert!(cache.evict_one_leaf());
        assert!(cache.evict_one_leaf());
        assert!(!cache.evict_one_leaf());
    }

    #[test]
    fn multi_block_prefix_eviction() {
        let mut cache = PrefixCache::default();
        cache.insert_prefix(&tokens(16), 16);
        cache.insert_prefix(&tokens(32), 16);
        cache.insert_prefix(&tokens(48), 16);
        assert_eq!(cache.bytes_used(16, 1), 48);

        assert!(cache.evict_one_leaf());
        assert!(cache.evict_one_leaf());
        assert!(cache.evict_one_leaf());
        assert!(!cache.evict_one_leaf());
        assert_eq!(cache.bytes_used(16, 1), 0);
    }
}
