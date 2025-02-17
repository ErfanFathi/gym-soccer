import os, subprocess, time, signal
import numpy as np
import gym
from gym import error, spaces
from gym import utils
from gym.utils import seeding

import socket
from contextlib import closing

try:
    import hfo_py
except ImportError as e:
    raise error.DependencyNotInstalled("{}. (HINT: you can install HFO dependencies with 'pip install gym[soccer].')".format(e))

import logging
logger = logging.getLogger(__name__)

def find_free_port():
    """Find a random free port. Does not guarantee that the port will still be free after return.
    Note: HFO takes three consecutive port numbers, this only checks one.

    Source: https://github.com/crowdAI/marLo/blob/master/marlo/utils.py

    :rtype:  `int`
    """

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(('', 0))
        return s.getsockname()[1]


class SoccerGoalieEnv(gym.Env, utils.EzPickle):
    metadata = {'render.modes': ['human']}

    def __init__(self):
        self.viewer = None
        self.server_process = None
        self.server_port = None
        self.hfo_path = hfo_py.get_hfo_path()
        print(self.hfo_path)
        self._configure_environment()
        self.env = hfo_py.HFOEnvironment()
        self.env.connectToServer(team_name="base_right", play_goalie=True, config_dir=hfo_py.get_config_path(), server_port=self.server_port)
        print("Shape =",self.env.getStateSize())
        self.observation_space = spaces.Box(low=-1, high=1,
                                            shape=((self.env.getStateSize(),)), dtype=np.float32)
        # Action space omits the Tackle/Catch actions, which are useful on defense
        low0 = np.array([0, -180], dtype=np.float32) 
        high0 = np.array([100, 180], dtype=np.float32)
        low1 = np.array([-180], dtype=np.float32)
        high1 = np.array([180], dtype=np.float32)
        low2 = np.array([0, -180], dtype=np.float32)
        high2 = np.array([100, 180], dtype=np.float32)
        low3 = np.array([0], dtype=np.float32)
        high3 = np.array([100], dtype=np.float32)
        self.action_space = spaces.Tuple((spaces.Discrete(5),
                                          spaces.Box(low=low0, high=high0, dtype=np.float32),
                                          spaces.Box(low=low1, high=high1, dtype=np.float32),
                                          spaces.Box(low=low2, high=high2, dtype=np.float32),
                                          spaces.Box(low=low3, high=high3, dtype=np.float32)))

        self.status = hfo_py.IN_GAME
        self._seed = -1

    def __del__(self):
        self.env.act(hfo_py.QUIT)
        self.env.step()
        os.kill(self.server_process.pid, signal.SIGINT)
        if self.viewer is not None:
            os.kill(self.viewer.pid, signal.SIGKILL)

    def _configure_environment(self):
        """
        Provides a chance for subclasses to override this method and supply
        a different server configuration. By default, we initialize one
        offense agent against no defenders.
        """
        self._start_hfo_server()

    def _start_hfo_server(self, frames_per_trial=500,
                          #untouched_time=1000, 
                          untouched_time=100, 
                          offense_agents=0,
                          defense_agents=1, offense_npcs=1,
                          defense_npcs=0, sync_mode=True, port=None,
                          offense_on_ball=1, fullstate=True, seed=-1,
                          ball_x_min=0.6, ball_x_max=0.6,
                          verbose=False, log_game=False,
                          log_dir="log"):
        """
        Starts the Half-Field-Offense server.
        frames_per_trial: Episodes end after this many steps.
        untouched_time: Episodes end if the ball is untouched for this many steps.
        offense_agents: Number of user-controlled offensive players.
        defense_agents: Number of user-controlled defenders.
        offense_npcs: Number of offensive bots.
        defense_npcs: Number of defense bots.
        sync_mode: Disabling sync mode runs server in real time (SLOW!).
        port: Port to start the server on.
        offense_on_ball: Player to give the ball to at beginning of episode.
        fullstate: Enable noise-free perception.
        seed: Seed the starting positions of the players and ball.
        ball_x_[min/max]: Initialize the ball this far downfield: [0,1]
        verbose: Verbose server messages.
        log_game: Enable game logging. Logs can be used for replay + visualization.
        log_dir: Directory to place game logs (*.rcg).
        """
        if port is None:
            port = find_free_port()
        self.server_port = port
        '''cmd = self.hfo_path + \
              " --headless --frames-per-trial %i --untouched-time %i --offense-agents %i"\
	      " --defense-agents %i --offense-npcs %i --defense-npcs %i"\
	      " --port %i --offense-on-ball %i --seed %i --ball-x-min %f"\
	      " --ball-x-max %f --log-dir %s"\
	      % (frames_per_trial, untouched_time, 
		 offense_agents,
		 defense_agents, offense_npcs, defense_npcs, port,
		 offense_on_ball, seed, ball_x_min, ball_x_max,
		 log_dir)'''
        cmd = self.hfo_path + \
              " --headless --frames-per-trial %i --offense-agents %i"\
              " --defense-agents %i --offense-npcs %i --defense-npcs %i"\
              " --port %i --offense-on-ball %i --seed %i --ball-x-min %f"\
              " --ball-x-max %f --log-dir %s"\
              % (frames_per_trial,
                 offense_agents,
                 defense_agents, offense_npcs, defense_npcs, port,
                 offense_on_ball, seed, ball_x_min, ball_x_max,
                 log_dir)
        if not sync_mode: cmd += " --no-sync"
        if fullstate:     cmd += " --fullstate"
        if verbose:       cmd += " --verbose"
        if not log_game:  cmd += " --no-logging"
        print('Starting server with command: %s' % cmd)
        self.server_process = subprocess.Popen(cmd.split(' '), shell=False)
        time.sleep(10) # Wait for server to startup before connecting a player

    def _start_viewer(self):
        """
        Starts the SoccerWindow visualizer. Note the viewer may also be
        used with a *.rcg logfile to replay a game. See details at
        https://github.com/LARG/HFO/blob/master/doc/manual.pdf.
        """
        cmd = hfo_py.get_viewer_path() +\
              " --connect --port %d" % (self.server_port)
        self.viewer = subprocess.Popen(cmd.split(' '), shell=False)

    def _step(self, action):
        self._take_action(action)
        self.status = self.env.step()
        reward = self._get_reward()
        ob = self.env.getState()
        episode_over = self.status != hfo_py.IN_GAME
        return ob, reward, episode_over, {'status': STATUS_LOOKUP[self.status]}

    def _take_action(self, action):
        """ Converts the action space into an HFO action. """
        action_type = ACTION_LOOKUP[action[0]]
        if action_type == hfo_py.DASH:
            self.env.act(action_type, action[1], action[2])
        elif action_type == hfo_py.TURN:
            self.env.act(action_type, action[3])
        elif action_type == hfo_py.KICK:
            self.env.act(action_type, action[4], action[5])
        elif action_type == hfo_py.TACKLE:
            self.env.act(action_type, action[6])
        elif action_type == hfo_py.CATCH:
            self.env.act(action_type)
        else:
            print('Unrecognized action %d' % action_type)
            self.env.act(hfo_py.NOOP)

    def _get_reward(self):
        if self.status == hfo_py.GOAL:
            return -1
        elif self.status == hfo_py.OUT_OF_BOUNDS or self.status == hfo_py.OUT_OF_TIME or self.status == hfo_py.CAPTURED_BY_DEFENSE:
            return 1
        else:
            return 0

    def _reset(self):
        """ Repeats NO-OP action until a new episode begins. """
        while self.status == hfo_py.IN_GAME:
            self.env.act(hfo_py.NOOP)
            self.status = self.env.step()
        while self.status != hfo_py.IN_GAME:
            self.env.act(hfo_py.NOOP)
            self.status = self.env.step()
            # prevent infinite output when server dies
            if self.status == hfo_py.SERVER_DOWN:
                raise ServerDownException("HFO server down!")
        return self.env.getState()

    def _render(self, mode='human', close=False):
        """ Viewer only supports human mode currently. """
        if close:
            if self.viewer is not None:
                os.kill(self.viewer.pid, signal.SIGKILL)
        else:
            if self.viewer is None:
                self._start_viewer()
 
    def close(self):
        if self.server_process is not None:
            try:
                os.kill(self.server_process.pid, signal.SIGKILL)
            except Exception:
                pass


class ServerDownException(Exception):
    """
    Custom error so agents can catch it and exit cleanly if the server dies unexpectedly.
    """
    pass
  

ACTION_LOOKUP = {
    0 : hfo_py.DASH,
    1 : hfo_py.TURN,
    2 : hfo_py.KICK,
    3 : hfo_py.TACKLE, # Used on defense to slide tackle the ball
    4 : hfo_py.CATCH,  # Used only by goalie to catch the ball
}

STATUS_LOOKUP = {
    hfo_py.IN_GAME: 'IN_GAME',
    hfo_py.SERVER_DOWN: 'SERVER_DOWN',
    hfo_py.GOAL: 'GOAL',
    hfo_py.OUT_OF_BOUNDS: 'OUT_OF_BOUNDS',
    hfo_py.OUT_OF_TIME: 'OUT_OF_TIME',
    hfo_py.CAPTURED_BY_DEFENSE: 'CAPTURED_BY_DEFENSE',
}