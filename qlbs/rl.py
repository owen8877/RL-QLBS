from itertools import chain
from typing import Tuple
from unittest import TestCase

import numpy as np
import torch
import tqdm
from gym import Env
from matplotlib import pyplot as plt
from torch.optim.adam import Adam
from tqdm import trange

import util
from qlbs.bs import BSPolicy, BSInitialEstimator, BSBaseline
from qlbs.env import State, Info, QLBSEnv, Policy, Baseline
from util.net import ResNet
from util.sample import geometricBM


class GaussianPolicy(Policy):
    def __init__(self, alpha):
        super().__init__()
        self.theta_mu = ResNet(8, 10, groups=2, layer_per_group=2)
        self.theta_sigma = ResNet(8, 10, groups=2, layer_per_group=2)

        self.optimizer = Adam(chain(self.theta_mu.parameters(), self.theta_sigma.parameters()), lr=alpha)

    def _gauss_param(self, tensor):
        tensor = tensor.float()
        mu = self.theta_mu(tensor)
        sigma = self.theta_sigma(tensor)

        mu_c = torch.sigmoid(mu)
        sigma_s = torch.sigmoid(sigma)

        sigma_c = torch.clip(sigma_s, 3e-2, 1)

        return mu_c, sigma_c

    def action(self, state, info):
        tensor = state.to_tensor(info)
        with torch.no_grad():
            mu, sigma = self._gauss_param(tensor)
            return float(np.random.randn(1)) * float(sigma) + float(mu)

    def update(self, delta, action, state, info):
        tensor = state.to_tensor(info)

        def loss(delta, a, tensor):
            mu, sigma = self._gauss_param(tensor)
            log_pi = - (a - mu) ** 2 / (2 * sigma ** 2) - torch.log(sigma)
            loss = - delta * log_pi
            loss.backward()
            return loss

        self.optimizer.zero_grad()
        self.optimizer.step(lambda: loss(delta, action, tensor))

    def batch_action(self, state_info_tensor):
        """
        :param state_info_tensor: [[normal_price, remaining_real_time, normal_strike_price, r, mu, sigma, risk_lambda]]
        :return:
        """
        with torch.no_grad():
            mu, sigma = self._gauss_param(state_info_tensor)
            return (torch.randn(len(mu)) * sigma[:, 0] + mu[:, 0]).numpy()

    def save(self, filename: str):
        util.ensure_dir(filename, need_strip_end=True)
        torch.save({
            'mu_net': self.theta_mu.state_dict(),
            'sigma_net': self.theta_sigma.state_dict(),
        }, filename)

    def load(self, filename: str):
        state_dict = torch.load(filename)
        self.theta_mu.load_state_dict(state_dict['mu_net'])
        self.theta_mu.eval()
        self.theta_sigma.load_state_dict(state_dict['sigma_net'])
        self.theta_sigma.eval()

    def train_based_on(self, source, target, lr, itr_max):
        optimizer = Adam(chain(self.theta_mu.parameters(), self.theta_sigma.parameters()), lr=lr)
        pbar = trange(int(itr_max))
        for _ in pbar:
            def loss_func():
                mu, sigma = self._gauss_param(source)
                prediction = torch.randn(len(mu)) * sigma[:, 0] + mu[:, 0]
                loss = torch.mean((prediction - target) ** 2)
                # loss = torch.mean((mu[:, 0] - target) ** 2) + torch.mean((sigma[:, 0] - 0.2) ** 2)
                loss.backward()
                return loss

            optimizer.zero_grad()
            loss = optimizer.step(loss_func)
            pbar.set_description(desc=f'loss={loss:.5e}')


class NNBaseline(Baseline):
    def __init__(self, alpha=1e-2):
        super().__init__()
        self.net = ResNet(8, 10, groups=2, layer_per_group=2)
        self.optimizer = Adam(self.net.parameters(), lr=alpha)

    def __call__(self, state: State, info: Info):
        tensor = state.to_tensor(info)
        with torch.no_grad():
            return float(self.net(tensor.float()))

    def update(self, G: float, state: State, info: Info):
        tensor = state.to_tensor(info)

        def loss(G, tensor):
            loss = (G - self.net(tensor.float())) ** 2
            loss.backward()
            return loss

        self.optimizer.zero_grad()
        self.optimizer.step(lambda: loss(G, tensor))

    def batch_estimate(self, state_info_tensor):
        with torch.no_grad():
            return self.net(state_info_tensor.float()).numpy()

    def save(self, filename: str):
        util.ensure_dir(filename, need_strip_end=True)
        torch.save({
            'net': self.net.state_dict(),
        }, filename)

    def load(self, filename: str):
        state_dict = torch.load(filename)
        self.net.load_state_dict(state_dict['net'])
        self.net.eval()

    def train_based_on(self, source, target, lr, itr_max):
        optimizer = Adam(self.net.parameters(), lr=lr)
        pbar = trange(int(itr_max))
        for _ in pbar:
            def loss_func():
                prediction = self.net(source.float())[:, 0]
                loss = torch.mean((prediction - target) ** 2)
                loss.backward()
                return loss

            optimizer.zero_grad()
            loss = optimizer.step(loss_func)
            pbar.set_description(desc=f'loss={loss:.5e}')


class SimplifiedGaussianPolicy(Policy):
    def __init__(self, alpha):
        super().__init__()
        self.theta_mu = ResNet(8, 10, groups=2, layer_per_group=2)
        self.theta_sigma = ResNet(8, 10, groups=2, layer_per_group=2)

        self.optimizer = Adam(chain(self.theta_mu.parameters(), self.theta_sigma.parameters()), lr=alpha)

    def _gauss_param(self, tensor):
        tensor = tensor.float()
        mu = self.theta_mu(tensor)
        sigma = self.theta_sigma(tensor)

        mu_c = torch.sigmoid(mu)
        sigma_s = torch.sigmoid(sigma)

        sigma_c = torch.clip(sigma_s, 3e-2, 1)

        return mu_c, sigma_c

    def action(self, state, info):
        tensor = state.to_tensor(info)
        with torch.no_grad():
            mu, sigma = self._gauss_param(tensor)
            return float(np.random.randn(1)) * float(sigma) + float(mu)

    def update(self, delta, action, state, info):
        tensor = state.to_tensor(info)

        def loss(delta, a, tensor):
            mu, sigma = self._gauss_param(tensor)
            log_pi = - (a - mu) ** 2 / (2 * sigma ** 2) - torch.log(sigma)
            loss = - delta * log_pi
            loss.backward()
            return loss

        self.optimizer.zero_grad()
        self.optimizer.step(lambda: loss(delta, action, tensor))

    def batch_action(self, state_info_tensor):
        """
        :param state_info_tensor: [[normal_price, remaining_real_time, normal_strike_price, r, mu, sigma, risk_lambda]]
        :return:
        """
        with torch.no_grad():
            mu, sigma = self._gauss_param(state_info_tensor)
            return (torch.randn(len(mu)) * sigma[:, 0] + mu[:, 0]).numpy()

    def save(self, filename: str):
        util.ensure_dir(filename, need_strip_end=True)
        torch.save({
            'mu_net': self.theta_mu.state_dict(),
            'sigma_net': self.theta_sigma.state_dict(),
        }, filename)

    def load(self, filename: str):
        state_dict = torch.load(filename)
        self.theta_mu.load_state_dict(state_dict['mu_net'])
        self.theta_mu.eval()
        self.theta_sigma.load_state_dict(state_dict['sigma_net'])
        self.theta_sigma.eval()

    def train_based_on(self, source, target, lr, itr_max):
        optimizer = Adam(chain(self.theta_mu.parameters(), self.theta_sigma.parameters()), lr=lr)
        pbar = trange(int(itr_max))
        for _ in pbar:
            def loss_func():
                mu, sigma = self._gauss_param(source)
                prediction = torch.randn(len(mu)) * sigma[:, 0] + mu[:, 0]
                loss = torch.mean((prediction - target) ** 2)
                # loss = torch.mean((mu[:, 0] - target) ** 2) + torch.mean((sigma[:, 0] - 0.2) ** 2)
                loss.backward()
                return loss

            optimizer.zero_grad()
            loss = optimizer.step(loss_func)
            pbar.set_description(desc=f'loss={loss:.5e}')


class SimplifiedNNBaseline(Baseline):
    def __init__(self, alpha=1e-2):
        super().__init__()
        self.net = ResNet(8, 10, groups=2, layer_per_group=2)
        self.optimizer = Adam(self.net.parameters(), lr=alpha)

    def __call__(self, state: State, info: Info):
        tensor = state.to_tensor(info)
        with torch.no_grad():
            return float(self.net(tensor.float()))

    def update(self, G: float, state: State, info: Info):
        tensor = state.to_tensor(info)

        def loss(G, tensor):
            loss = (G - self.net(tensor.float())) ** 2
            loss.backward()
            return loss

        self.optimizer.zero_grad()
        self.optimizer.step(lambda: loss(G, tensor))

    def batch_estimate(self, state_info_tensor):
        with torch.no_grad():
            return self.net(state_info_tensor.float()).numpy()

    def save(self, filename: str):
        util.ensure_dir(filename, need_strip_end=True)
        torch.save({
            'net': self.net.state_dict(),
        }, filename)

    def load(self, filename: str):
        state_dict = torch.load(filename)
        self.net.load_state_dict(state_dict['net'])
        self.net.eval()

    def train_based_on(self, source, target, lr, itr_max):
        optimizer = Adam(self.net.parameters(), lr=lr)
        pbar = trange(int(itr_max))
        for _ in pbar:
            def loss_func():
                prediction = self.net(source.float())[:, 0]
                loss = torch.mean((prediction - target) ** 2)
                loss.backward()
                return loss

            optimizer.zero_grad()
            loss = optimizer.step(loss_func)
            pbar.set_description(desc=f'loss={loss:.5e}')

def policy_gradient(env: Env, pi: Policy, V: Baseline, episode_n: int, *, ax: plt.Axes = None,
                    axs_env: Tuple[plt.Axes] = None, V_frozen: bool = False, pi_frozen: bool = False):
    t0_returns = []
    t0_risks = []
    collector = util.EMACollector(half_life=100, Q_price=None, cashflow_price=None)
    if ax is None:
        fig, ax = plt.subplots()
    pbar = trange(episode_n)

    ##########
    __price_t_Gs = []
    ##########

    for e in pbar:
        states = []
        actions = []
        rewards = []
        risks = []

        (state, info), done = env.reset(), False
        states.append(state)
        while not done:
            action = pi.action(state, info)
            state, reward, done, additional = env.step(action, pi)
            if e == episode_n - 1 and axs_env is not None:
                env.render(axs=axs_env)

            states.append(state)
            actions.append(action)
            rewards.append(reward)
            risks.append(additional['risk'])

        if V is not None:
            T = len(actions)
            G_tmp = 0
            Gs_rev = []
            for t in range(T - 1, -1, -1):
                G_tmp = G_tmp * env.gamma + rewards[t]
                Gs_rev.append(G_tmp)
            Gs = Gs_rev[::-1]

            for t in range(T):
                delta = Gs[t] - V(states[t], info)
                __price_t_Gs.append([states[t].normalized_asset_price, t, Gs[t]])
                if not V_frozen:
                    V.update(Gs[t], states[t], info)
                if not pi_frozen:
                    pi.update(delta * np.power(env.gamma, t), actions[t], states[t], info)

        discount = np.power(env.gamma, np.arange(len(rewards)))
        t0_return = np.dot(rewards, discount)
        t0_risk = env.info.risk_lambda * np.dot(risks, discount) * info._dt

        collector.append(Q_price=-t0_return, cashflow_price=-t0_return - t0_risk)
        t0_returns.append(t0_return)
        t0_risks.append(t0_risk)
        pbar.set_description(
            f't0_return={t0_return:.2e};t0_risks={t0_risk:.2e};r={info.r:.4f};mu={info.mu:.4f};sigma={info.sigma:.4f};K={info.strike_price:.4f}')
        if (e + 1) % 100 == 0 and ax is not None:
            # indices = np.arange(0, e + 1)
            ax.cla()
            # option_price = np.array(t0_returns) * (-1)
            # ax.plot(indices, option_price)
            # ax.plot(indices, option_price + np.array(t0_risks), ls=':')
            # ax.plot(indices, option_price - np.array(t0_risks), ls=':')
            # ax.plot(indices, np.cumsum(t0_returns) / (1 + indices) * (-1), ls='--')
            collector.plot(ax)
            ax.set(ylabel='negative reward (=option price)')
            plt.show(block=False)
            plt.pause(0.01)

    np.save('tmp.npy', np.array(__price_t_Gs))

    return t0_returns


class Test(TestCase):
    class HedgeEnv:
        def __init__(self, remaining_till, is_call_option, _dt: float = 1):
            self.state = State(0, 0)
            self.remaining_till = remaining_till
            self.is_call_option = is_call_option
            self._dt = _dt

        def reset(self, info: Info, asset_normalized_prices: np.ndarray, asset_standard_prices: np.ndarray):
            self.info = info
            self.gamma = np.exp(-self.info.r * self._dt)
            self.asset_normalized_prices = asset_normalized_prices
            self.asset_standard_prices = asset_standard_prices

            self.portfolio_value = util.payoff_of_option(self.is_call_option, self.asset_standard_prices[-1],
                                                         self.info.strike_price)
            self.state.remaining_step = 1
            self.state.normalized_asset_price = self.asset_normalized_prices[-2]
            return self.state

        def step(self, hedge) -> Tuple[State, float, float, bool]:
            rt = self.state.remaining_step
            dS = self.asset_standard_prices[-rt] - self.asset_standard_prices[-rt - 1] / self.gamma
            in_position_change = self.gamma * hedge * dS
            self.portfolio_value *= self.gamma
            self.portfolio_value -= in_position_change

            self.state.remaining_step = rt + 1
            done = self.state.remaining_step > self.remaining_till
            if not done:
                self.state.normalized_asset_price = self.asset_normalized_prices[-rt - 2]
            return self.state, self.portfolio_value, in_position_change, done

    def test_hedge_env_bs(self):
        from matplotlib import pyplot as plt
        import matplotlib as mpl
        from tqdm import trange
        import seaborn as sns
        import pandas as pd
        from qlbs.bs import BSInitialEstimator, BSPolicy
        mpl.use('TkAgg')
        sns.set_style('whitegrid')

        is_call_option = True
        r = 0e-3
        mu = 0e-3
        sigma = 5e-3
        risk_lambda = 1
        initial_price = 1
        strike_price = 1.001
        T = 10
        _dt = 0.01

        max_time = int(np.round(T / _dt))
        env = Test.HedgeEnv(remaining_till=max_time, is_call_option=is_call_option)
        bs_pi = BSPolicy(is_call=is_call_option)
        bs_estimator = BSInitialEstimator(is_call_option)

        initial_errors = []
        linf_errors = []
        for _ in trange(10):
            standard_prices, normalized_prices = geometricBM(initial_price, max_time, 1, mu, sigma, _dt)
            standard_prices = standard_prices[0, :]
            normalized_prices = normalized_prices[0, :]
            info = Info(strike_price=strike_price, r=r, mu=mu, sigma=sigma, risk_lambda=risk_lambda, _dt=_dt)
            state = env.reset(info, normalized_prices, standard_prices)
            done = False

            bs_option_prices = np.array(
                [bs_estimator(standard_prices[t], strike_price, max_time - t, r, sigma, _dt) for t in
                 range(max_time + 1)])

            pvs = np.zeros(max_time + 1)
            hedges = np.zeros(max_time + 1)
            pvs[-1] = util.payoff_of_option(is_call_option, standard_prices[-1], strike_price)
            while not done:
                hedge = bs_pi.action(state, info)
                state, pv, in_position_change, done = env.step(hedge)
                pvs[-state.remaining_step] = pv
                hedges[-state.remaining_step] = hedge

            initial_errors.append(pvs[0] - bs_option_prices[0])
            linf_errors.append(np.linalg.norm(pvs - bs_option_prices, ord=np.inf))

            fig, (ax_price, ax_option, ax_hedge) = plt.subplots(3, 1, figsize=(4, 5))
            times = np.arange(0, max_time + 1)
            ax_price.plot(times, standard_prices)
            ax_price.set(ylabel='stock price')
            ax_option.plot(times, pvs, ls='--', label='portfolio')
            ax_option.plot(times, bs_option_prices, label='bs price')
            ax_option.legend(loc='best')
            ax_hedge.plot(times, hedges)

            plt.show(block=True)

        sns.histplot(pd.DataFrame({'initial': initial_errors, 'inf': linf_errors}))
        plt.show(block=True)

    def test_qlbs_env(self):
        import matplotlib as mpl
        import seaborn as sns
        from qlbs.bs import BSPolicy
        mpl.use('TkAgg')
        sns.set_style('whitegrid')

        is_call_option = True
        r = 1e-2
        mu = 0e-3
        sigma = 1e-1
        risk_lambda = 1
        initial_price = 0.9
        strike_price = 1
        T = 3
        _dt = 1

        max_time = int(np.round(T / _dt))
        env = QLBSEnv(is_call_option=is_call_option, strike_price=strike_price, max_step=max_time, mu=mu, sigma=sigma,
                      r=r, risk_lambda=risk_lambda, initial_asset_price=initial_price, risk_simulation_paths=50,
                      _dt=_dt, mutation=0)
        bs_pi = BSPolicy(is_call=is_call_option)

        policy_gradient(env, bs_pi, None, episode_n=500)
        plt.show()

    def test_gaussian_policy_training(self):
        import matplotlib as mpl
        import seaborn as sns
        mpl.use('TkAgg')
        sns.set_style('whitegrid')

        is_call_option = True
        r = 1e-2
        mu = 0e-3
        sigma = 1e-1
        risk_lambda = 1
        initial_price = 1
        strike_price = 1
        T = 3
        _dt = 1

        max_time = int(np.round(T / _dt))
        env = QLBSEnv(is_call_option=is_call_option, strike_price=strike_price, max_step=max_time, mu=mu, sigma=sigma,
                      r=r, risk_lambda=risk_lambda, initial_asset_price=initial_price, risk_simulation_paths=50,
                      _dt=_dt, mutation=1e-1)
        gaussian_pi = GaussianPolicy(alpha=1e-4)
        nnbaseline = NNBaseline(alpha=1e-4)
        bs_pi = BSPolicy(is_call=is_call_option)
        bs_baseline = BSBaseline(is_call=is_call_option)

        load_plan = 'train_to_bs_test'
        # load_plan = 'T5_test'
        nnbaseline.load(f'../dataset/trained_model/qlbs/{load_plan}/baseline.pt')
        gaussian_pi.load(f'../dataset/trained_model/qlbs/{load_plan}/policy.pt')
        # policy_gradient(env, gaussian_pi, nnbaseline, episode_n=2000, pi_frozen=False)
        policy_gradient(env, bs_pi, nnbaseline, episode_n=2000, pi_frozen=True)
        save_plan = 'rl_after_pretrain'
        # save_plan = 'T5_test'
        gaussian_pi.save(f'../dataset/trained_model/qlbs/{save_plan}/policy.pt')
        nnbaseline.save(f'../dataset/trained_model/qlbs/{save_plan}/baseline.pt')
        plt.show()

    def test_examine_trained_model(self):
        import matplotlib as mpl
        import seaborn as sns
        mpl.use('TkAgg')
        sns.set_style('whitegrid')

        is_call_option = True
        r = 1e-2
        mu = 0e-3
        sigma = 1e-2
        risk_lambda = 3
        initial_price = 1
        strike_price = 1
        max_step = 3
        _dt = 1

        # plan = 'T5_test'
        # plan = 'train_to_bs_test'
        plan = 'rl_after_pretrain'
        gaussian_pi = GaussianPolicy(alpha=1e-3)
        gaussian_pi.load(f'../dataset/trained_model/qlbs/{plan}/policy.pt')
        nnbaseline = NNBaseline(alpha=1e-3)
        nnbaseline.load(f'../dataset/trained_model/qlbs/{plan}/baseline.pt')
        bs_pi = BSPolicy(is_call=is_call_option)
        bs_baseline = BSBaseline(is_call=is_call_option)

        # max_time = int(np.round(max_step / _dt))
        # env = QLBSEnv(is_call_option=is_call_option, strike_price=strike_price, max_step=max_time, mu=mu, sigma=sigma,
        #               r=r, risk_lambda=risk_lambda, initial_asset_price=initial_price, risk_simulation_paths=50,
        #               _dt=_dt, mutation=0)
        # policy_gradient(env, bs_pi, None, episode_n=10000)

        T_grid, S_grid = np.meshgrid(np.arange(max_step + 1),
                                     np.linspace(strike_price * 0.8, strike_price * 1.2, 91))
        T_long = T_grid.reshape(-1, 1)[:, 0]
        S_long = S_grid.reshape(-1, 1)[:, 0]

        T1_grid, S1_grid = np.meshgrid(np.arange(max_step),
                                       np.linspace(strike_price * 0.8, strike_price * 1.2, 91))
        T1_long = T1_grid.reshape(-1, 1)[:, 0]
        S1_long = S1_grid.reshape(-1, 1)[:, 0]

        def build_state_info_tensor(price, time):
            time = torch.tensor(time)
            price = torch.tensor(price)
            state_info_tensor = torch.empty((len(time), 8))
            state_info_tensor[:, 0] = torch.tensor(
                util.standard_to_normalized_price(price.numpy(), mu, sigma, max_step - time.numpy(),
                                                  _dt))  # normal_price
            state_info_tensor[:, 1] = time * _dt  # passed_real_time
            state_info_tensor[:, 2] = (max_step - time) * _dt  # remaining_real_time
            state_info_tensor[:, 3] = torch.tensor(
                util.standard_to_normalized_price(strike_price, mu, sigma, max_step - time.numpy(),
                                                  _dt))  # normal_strike_price
            state_info_tensor[:, 4] = r  # r
            state_info_tensor[:, 5] = mu  # mu
            state_info_tensor[:, 6] = sigma  # sigma
            state_info_tensor[:, 7] = risk_lambda  # risk_lambda
            return state_info_tensor

        price_tensor = build_state_info_tensor(S_long, T_long)
        bs_price = bs_baseline.batch_estimate(price_tensor.numpy())
        nn_price = nnbaseline.batch_estimate(price_tensor)
        hedge_tensor = build_state_info_tensor(S1_long, T1_long)
        bs_hedge = bs_pi.batch_action(hedge_tensor)
        nn_hedge = gaussian_pi.batch_action(hedge_tensor)

        fig: plt.Figure = plt.figure(figsize=(10, 8))
        ax11 = fig.add_subplot(2, 2, 1, projection='3d')
        ax11.plot_surface(T_grid * _dt, S_grid, bs_price.reshape(T_grid.shape))
        ax11.set_title('BS price')

        ax12 = fig.add_subplot(2, 2, 2, projection='3d')
        ax12.plot_surface(T_grid * _dt, S_grid, -nn_price.reshape(T_grid.shape))
        ax12.set_title('NN price')

        ax21 = fig.add_subplot(2, 2, 3, projection='3d')
        ax21.plot_surface(T1_grid * _dt, S1_grid, bs_hedge.reshape(T1_grid.shape))
        ax21.set_title('BS hedge')

        ax22 = fig.add_subplot(2, 2, 4, projection='3d')
        ax22.plot_surface(T1_grid * _dt, S1_grid, nn_hedge.reshape(T1_grid.shape))
        ax22.set_title('NN hedge')

        T_slice, S_slice = np.meshgrid(0,
                                       np.linspace(strike_price * 0.9, strike_price * 1.1, 31))
        T_slice = T_slice.reshape(-1, 1)[:, 0]
        S_slice = S_slice.reshape(-1, 1)[:, 0]

        fig2 = plt.figure(2, figsize=(7, 5))
        price_slice = build_state_info_tensor(S_slice, T_slice)
        plt.plot(S_slice, bs_baseline.batch_estimate(price_slice.numpy()), '--', label='BS')
        plt.plot(S_slice, -nnbaseline.batch_estimate(price_slice), label='NN')
        plt.legend(loc='best')

        fig = plt.figure()
        ax = fig.add_subplot(projection='3d')
        __array = np.load('tmp.npy')
        ax.scatter(__array[:, 0], __array[:, 1], -__array[:, 2])

        plt.show()

    def test_train_nn_with_bs(self):
        import matplotlib as mpl
        import seaborn as sns
        mpl.use('TkAgg')
        sns.set_style('whitegrid')

        is_call_option = True
        max_step = 4
        _dt = 1

        gaussian_pi = GaussianPolicy(alpha=1e-3)
        nnbaseline = NNBaseline(alpha=1e-3)
        bs_pi = BSPolicy(is_call=is_call_option)
        bs_baseline = BSBaseline(is_call=is_call_option)

        def build_random(RS, include_terminal: bool):
            time = np.random.randint(low=0, high=max_step + 1 if include_terminal else max_step, size=RS)
            price = np.exp(np.random.randn(RS) * 0.025)
            strike_price = np.exp(np.random.randn(RS) * 0.025)
            state_info_tensor = np.empty((RS, 8))

            state_info_tensor[:, 4] = np.abs(np.random.randn(RS) * 5e-3)  # r
            mu = np.random.randn(RS) * 3e-3
            state_info_tensor[:, 5] = mu  # mu
            sigma = np.abs(np.random.randn(RS)) * 1e-1
            state_info_tensor[:, 6] = sigma  # sigma
            state_info_tensor[:, 7] = np.abs(np.random.randn(RS))  # risk_lambda

            state_info_tensor[:, 0] = util.standard_to_normalized_price(price, mu, sigma, max_step - time,
                                                                        _dt)  # normal_price
            state_info_tensor[:, 1] = time * _dt  # passed_real_time
            state_info_tensor[:, 2] = (max_step - time) * _dt  # remaining_real_time
            state_info_tensor[:, 3] = util.standard_to_normalized_price(strike_price, mu, sigma, max_step - time,
                                                                        _dt)  # normal_strike_price

            return torch.tensor(state_info_tensor)

        RS = 10000
        source_price = build_random(RS, include_terminal=True)
        source_hedge = build_random(RS, include_terminal=False)
        target_price = torch.tensor(-bs_baseline.batch_estimate(source_price.numpy()))
        target_hedge = torch.tensor(bs_pi.batch_action(source_hedge))

        gaussian_pi.load('../dataset/trained_model/qlbs/train_to_bs_test/policy.pt')
        gaussian_pi.train_based_on(source_hedge, target_hedge, lr=1e-3, itr_max=2e3)
        gaussian_pi.save('../dataset/trained_model/qlbs/train_to_bs_test/policy.pt')

        nnbaseline.load('../dataset/trained_model/qlbs/train_to_bs_test/baseline.pt')
        nnbaseline.train_based_on(source_price, target_price, lr=1e-3, itr_max=2e3)
        nnbaseline.save('../dataset/trained_model/qlbs/train_to_bs_test/baseline.pt')
