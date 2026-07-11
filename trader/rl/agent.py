"""A small, self-contained DQN that drives a TensorTrade `TradingEnv`.

Why not `tensortrade.agents.DQNAgent`? Its shipped agent calls
`tf.keras.optimizers.Adam(lr=...)`, which Keras 3 removed -- so the bundled
agents are dead on any modern TensorFlow. Rather than pin an ancient TF, we
supply a compact DQN here that uses the current Keras API. It is deliberately
minimal (a couple of Dense layers, experience replay, a target network,
epsilon-greedy) -- enough to learn a long/flat policy over the BSH env, cheap
enough to train on CPU during a deploy.

TensorFlow is imported lazily inside methods so importing this module (e.g. for
`RLTrader.decide`, which only needs a saved model) doesn't force a TF import
until you actually build/train a network.
"""
from __future__ import annotations

import json
import os
import random
from collections import deque

import numpy as np


def _unpack_step(ret):
    """Normalize gym 4-tuple and gymnasium 5-tuple step returns."""
    if len(ret) == 5:
        obs, reward, terminated, truncated, info = ret
        return obs, float(reward), bool(terminated or truncated), info
    obs, reward, done, info = ret
    return obs, float(reward), bool(done), info


def _unpack_reset(ret):
    return ret[0] if isinstance(ret, tuple) else ret


class DQNAgent:
    """Deep Q-Network over a flattened (window x features) observation."""

    def __init__(self, obs_shape, n_actions: int, hidden=(64, 32),
                 gamma=0.99, lr=1e-3, seed: int | None = 0):
        self.obs_shape = tuple(obs_shape)
        self.n_actions = int(n_actions)
        self.hidden = tuple(hidden)
        self.gamma = float(gamma)
        self.lr = float(lr)
        self.seed = seed
        self._model = None
        self._target = None
        self.meta: dict = {}

    # ---- network -------------------------------------------------------- #
    def _flat_dim(self) -> int:
        d = 1
        for s in self.obs_shape:
            d *= int(s)
        return d

    def _build(self):
        import tensorflow as tf
        if self.seed is not None:
            tf.random.set_seed(self.seed)
            np.random.seed(self.seed)
            random.seed(self.seed)
        model = tf.keras.Sequential(
            [tf.keras.layers.Input(shape=(self._flat_dim(),))]
            + [tf.keras.layers.Dense(h, activation="relu") for h in self.hidden]
            + [tf.keras.layers.Dense(self.n_actions, activation="linear")]
        )
        # Keras 3: use `learning_rate`, NOT `lr` (the bug in the bundled agent).
        model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=self.lr),
                      loss="mse")
        return model

    def _ensure(self):
        if self._model is None:
            self._model = self._build()
            self._target = self._build()
            self._target.set_weights(self._model.get_weights())

    # ---- inference ------------------------------------------------------ #
    def q_values(self, obs) -> np.ndarray:
        self._ensure()
        x = np.asarray(obs, dtype=np.float32).reshape(1, -1)
        return self._model.predict(x, verbose=0)[0]

    def act(self, obs, epsilon: float = 0.0) -> int:
        if epsilon > 0 and random.random() < epsilon:
            return random.randrange(self.n_actions)
        return int(np.argmax(self.q_values(obs)))

    # ---- training ------------------------------------------------------- #
    def train(self, env, episodes: int = 6, max_steps: int = 1000,
              batch_size: int = 32, buffer_size: int = 5000,
              eps_start: float = 1.0, eps_end: float = 0.05, eps_decay: float = 0.995,
              target_sync: int = 200, warmup: int = 200, verbose: bool = True):
        """Train against a TensorTrade env. Returns a list of per-episode rewards."""
        self._ensure()
        buffer: deque = deque(maxlen=buffer_size)
        eps = eps_start
        step_count = 0
        history = []
        for ep in range(episodes):
            obs = _unpack_reset(env.reset())
            obs = np.asarray(obs, dtype=np.float32)
            ep_reward = 0.0
            for _ in range(max_steps):
                a = self.act(obs, epsilon=eps)
                nobs, reward, done, _ = _unpack_step(env.step(a))
                nobs = np.asarray(nobs, dtype=np.float32)
                buffer.append((obs.reshape(-1), a, reward, nobs.reshape(-1), done))
                obs = nobs
                ep_reward += reward
                step_count += 1
                if len(buffer) >= max(batch_size, warmup):
                    self._replay(buffer, batch_size)
                if step_count % target_sync == 0:
                    self._target.set_weights(self._model.get_weights())
                if done:
                    break
            eps = max(eps_end, eps * eps_decay)
            history.append(ep_reward)
            if verbose:
                print(f"[rl] episode {ep + 1}/{episodes} reward={ep_reward:+.4f} eps={eps:.3f}")
        self.meta["episodes_trained"] = int(self.meta.get("episodes_trained", 0) + episodes)
        self.meta["last_rewards"] = [round(float(x), 5) for x in history]
        return history

    def _replay(self, buffer, batch_size: int):
        import tensorflow as tf  # noqa: F401  (kept local; models already built)
        batch = random.sample(buffer, batch_size)
        obs = np.array([b[0] for b in batch], dtype=np.float32)
        acts = np.array([b[1] for b in batch], dtype=np.int32)
        rews = np.array([b[2] for b in batch], dtype=np.float32)
        nobs = np.array([b[3] for b in batch], dtype=np.float32)
        done = np.array([b[4] for b in batch], dtype=np.float32)
        q_next = self._target.predict(nobs, verbose=0)
        targets = self._model.predict(obs, verbose=0)
        best_next = np.max(q_next, axis=1)
        for i in range(batch_size):
            targets[i, acts[i]] = rews[i] + (1.0 - done[i]) * self.gamma * best_next[i]
        self._model.fit(obs, targets, epochs=1, verbose=0)

    # ---- persistence ---------------------------------------------------- #
    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._ensure()
        self._model.save(path + ".keras")
        with open(path + ".meta.json", "w") as f:
            json.dump({"obs_shape": list(self.obs_shape), "n_actions": self.n_actions,
                       "hidden": list(self.hidden), "gamma": self.gamma, "lr": self.lr,
                       "meta": self.meta}, f, indent=2)

    @classmethod
    def load(cls, path: str) -> "DQNAgent | None":
        meta_path = path + ".meta.json"
        model_path = path + ".keras"
        if not (os.path.exists(meta_path) and os.path.exists(model_path)):
            return None
        import tensorflow as tf
        with open(meta_path) as f:
            d = json.load(f)
        agent = cls(d["obs_shape"], d["n_actions"], hidden=tuple(d.get("hidden", (64, 32))),
                    gamma=d.get("gamma", 0.99), lr=d.get("lr", 1e-3))
        agent._model = tf.keras.models.load_model(model_path)
        agent._target = tf.keras.models.load_model(model_path)
        agent.meta = d.get("meta", {})
        return agent
