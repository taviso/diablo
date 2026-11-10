#include <vector>
#include <queue>
#include <cmath>
#include <cstdint>
#include <algorithm>
#include <new>

using namespace std;

extern "C" {

// Exact Node-Weighted Steiner Tree solver using Dreyfus-Wagner dynamic programming.
// Computes the globally optimal Steiner tree for ALL valid subsets of board exits in a single pass.
//
// Returns:
//   0 on success
//  -1 if total_bits exceeds safe memory limit (total_bits > 20)
//  -2 if memory allocation or internal error occurs
int solve_board_all_exits(
    int V,
    const double* w,
    const int* deg,
    const int* adj_flat,
    int k_base,
    const int* comp_sizes,
    const int* comp_verts_flat,
    int num_exits,
    const int* exit_verts,
    int max_active_exits,
    double* out_costs,
    uint8_t* out_selected
) {
    try {
        if (V <= 0 || k_base < 0 || num_exits < 0 || num_exits > 4) {
            return -1;
        }

        int b_bits = (k_base > 0) ? (k_base - 1) : 0;
        int total_bits = b_bits + num_exits;
        if (total_bits > 20) {
            return -1;
        }

        vector<vector<int>> orig_adj(V);
        int pos = 0;
        for (int i = 0; i < V; ++i) {
            orig_adj[i].assign(adj_flat + pos, adj_flat + pos + deg[i]);
            pos += deg[i];
        }

        vector<vector<int>> comps(k_base);
        vector<int> comp_of(V, -1);
        pos = 0;
        for (int i = 0; i < k_base; ++i) {
            comps[i].assign(comp_verts_flat + pos, comp_verts_flat + pos + comp_sizes[i]);
            pos += comp_sizes[i];
            for (int u : comps[i]) {
                comp_of[u] = i;
            }
        }

        vector<uint8_t> is_exit_orig(V, 0);
        for (int e = 0; e < num_exits; ++e) {
            int u = exit_verts[e];
            if (u >= 0 && u < V) is_exit_orig[u] = 1;
        }

        // In any rooted Steiner arborescence, a non-terminal non-exit vertex of degree <= 2 has 1 parent and 1 child,
        // so it only participates in Dijkstra STEP transitions and never in O(3^k) MERGE transitions.
        vector<int> merge_verts;
        for (int u = 0; u < V; ++u) {
            if (comp_of[u] >= 0 || is_exit_orig[u] || orig_adj[u].size() >= 3) {
                merge_verts.push_back(u);
            }
        }

        size_t num_masks = 1ULL << total_bits;
        int full_base_mask = (1 << b_bits) - 1;

        const double INF = 1e18;
        vector<double> dp(num_masks * (size_t)V, INF);
        vector<uint8_t> p_type(num_masks * (size_t)V, 0);
        vector<int> p_arg1(num_masks * (size_t)V, -1);

        for (int c = 0; c < b_bits; ++c) {
            size_t m = 1ULL << c;
            for (int u : comps[c]) {
                dp[m * (size_t)V + u] = 0.0;
            }
        }
        for (int e = 0; e < num_exits; ++e) {
            int u = exit_verts[e];
            if (u >= 0 && u < V) {
                size_t m = 1ULL << (b_bits + e);
                dp[m * (size_t)V + u] = 0.0;
            }
        }

        for (size_t mask = 1; mask < num_masks; ++mask) {
            if (__builtin_popcount((unsigned int)(mask >> b_bits)) > max_active_exits) continue;
            double* dp_mask = &dp[mask * (size_t)V];
            uint8_t* pt_mask = &p_type[mask * (size_t)V];
            int* pa1_mask = &p_arg1[mask * (size_t)V];

            size_t lowbit = mask & (~mask + 1);
            for (size_t sub = (mask - 1) & mask; sub > 0; sub = (sub - 1) & mask) {
                if (sub & lowbit) {
                    size_t other = mask ^ sub;
                    const double* dp_sub = &dp[sub * (size_t)V];
                    const double* dp_other = &dp[other * (size_t)V];
                    for (int v : merge_verts) {
                        double cand = dp_sub[v] + dp_other[v];
                        if (cand < dp_mask[v]) {
                            dp_mask[v] = cand;
                            pt_mask[v] = 2;
                            pa1_mask[v] = (int)sub;
                        }
                    }
                }
            }

            priority_queue<pair<double, int>, vector<pair<double, int>>, greater<pair<double, int>>> pq;
            for (int v = 0; v < V; ++v) {
                if (dp_mask[v] < INF / 2) pq.push({dp_mask[v], v});
            }
            while (!pq.empty()) {
                auto [d, u] = pq.top();
                pq.pop();
                if (d > dp_mask[u]) continue;
                double cost_via_u = d + w[u];
                for (int nb : orig_adj[u]) {
                    if (cost_via_u < dp_mask[nb]) {
                        dp_mask[nb] = cost_via_u;
                        pt_mask[nb] = 1;
                        pa1_mask[nb] = u;
                        pq.push({cost_via_u, nb});
                    }
                }
            }
        }

        int num_exit_masks = 1 << num_exits;
        for (int emask = 0; emask < num_exit_masks; ++emask) {
            out_costs[emask] = INF;
            fill(out_selected + emask * V, out_selected + (emask + 1) * V, 0);
            if (__builtin_popcount((unsigned int)emask) > max_active_exits) continue;

            int full_mask = full_base_mask | (emask << b_bits);
            double best_cost = INF;
            int best_root = -1;

            if (k_base > 0) {
                if (full_mask == 0) {
                    best_cost = 0.0;
                    best_root = comps[k_base - 1][0];
                } else {
                    for (int u : comps[k_base - 1]) {
                        double c = dp[(size_t)full_mask * (size_t)V + u];
                        if (c < best_cost) {
                            best_cost = c;
                            best_root = u;
                        }
                    }
                }
            } else if (emask > 0) {
                int first_e = __builtin_ctz((unsigned int)emask);
                int rem_mask = full_mask ^ (1 << first_e);
                int u = exit_verts[first_e];
                if (u >= 0 && u < V) {
                    if (rem_mask == 0) {
                        best_cost = w[u];
                        best_root = u;
                    } else if (dp[(size_t)rem_mask * (size_t)V + u] < INF / 2) {
                        best_cost = dp[(size_t)rem_mask * (size_t)V + u] + w[u];
                        best_root = u;
                    }
                    full_mask = rem_mask;
                }
            }

            if (best_root < 0 || best_cost >= INF / 2) continue;
            out_costs[emask] = best_cost;

            uint8_t* sel = &out_selected[emask * V];
            for (int c = 0; c < k_base; ++c) {
                for (int u : comps[c]) sel[u] = 1;
            }
            for (int e = 0; e < num_exits; ++e) {
                if (((emask >> e) & 1) && exit_verts[e] >= 0 && exit_verts[e] < V) {
                    sel[exit_verts[e]] = 1;
                }
            }
            if (full_mask > 0) {
                vector<pair<int, int>> stack = {{full_mask, best_root}};
                while (!stack.empty()) {
                    auto [m, v] = stack.back();
                    stack.pop_back();
                    sel[v] = 1;
                    size_t idx = (size_t)m * (size_t)V + v;
                    if (p_type[idx] == 1) {
                        stack.push_back({m, p_arg1[idx]});
                    } else if (p_type[idx] == 2) {
                        int sub = p_arg1[idx];
                        stack.push_back({sub, v});
                        stack.push_back({m ^ sub, v});
                    }
                }
            }
        }
        return 0;
    } catch (...) {
        return -2;
    }
}

}
