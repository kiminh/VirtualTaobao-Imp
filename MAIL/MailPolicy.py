#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Created at 2019/12/5 下午2:59
from GAN_SD.GeneratorModel import GeneratorModel
from utils.replay_memory import Memory
from utils.utils import *


class MailPolicy(nn.Module):
    def __init__(self, activation=nn.LeakyReLU):
        super(MailPolicy, self).__init__()

        self.dim_user_state = 88 + 27 + 1
        self.dim_user_action = 11

        self.dim_engine_state = 88
        self.dim_engine_hidden = 256
        self.dim_engine_action = 27

        self.dim_userleave_state = 88
        self.dim_userleave_action = 101

        self.UserModel = GeneratorModel()
        self.UserModel.to(device).load()

        self.EnginePolicy = nn.Sequential(
            nn.Linear(self.dim_engine_state, self.dim_engine_hidden),
            activation(),
            nn.Linear(self.dim_engine_hidden, self.dim_engine_action),
        )

        self.UserPolicy = nn.Sequential(
            nn.Linear(self.dim_user_state, 128),
            activation(),
            nn.Linear(128, 256),
            activation(),
            nn.Linear(256, self.dim_user_action)
        )

        self.UserLeavePolicy = nn.Sequential(
            nn.Linear(self.dim_userleave_state, 128),
            nn.LeakyReLU(),
            nn.Linear(128, 256),
            nn.LeakyReLU(),
            nn.Linear(256, self.dim_userleave_action)
        )

        self.UserPolicy.apply(init_weight)
        self.EnginePolicy.apply(init_weight)
        self.UserLeavePolicy.apply(init_weight)

        self.memory = Memory()

        to_device(self.UserPolicy, self.EnginePolicy, self.UserLeavePolicy)

    def get_engine_action(self, engine_state):
        return self.EnginePolicy(engine_state)

    # user_state (user_feature, engine_action, page_index)
    def get_user_action_prob(self, user_state):
        action_prob = F.softmax(self.UserPolicy(user_state), dim=1)
        return action_prob

    def get_user_action(self, user_state):
        action_prob = self.get_user_action_prob(user_state)
        action = torch.argmax(action_prob, 1)
        return action, action_prob

    def get_user_leave_action(self, user):
        x = self.UserLeavePolicy(user)
        leave_page_index = torch.multinomial(F.softmax(x, dim=1), 1)
        return leave_page_index

    def generate_batch(self, mini_batch_size=5000):
        """
        generate enough (state, action) pairs into memory, at least min_batch_size items.
        ######################
        notice for one trajectory, plat_state and plat_action never change(may cause some questions)
        ######################
        :param mini_batch_size: steps to
        :return: None
        """
        self.memory.clear()

        num_items = 0  # count generated (state, action) pairs

        while num_items < mini_batch_size:
            # sample user from GAN-SD distribution
            plat_state, _ = self.UserModel.generate()

            # get user's leave page index from leave model
            leave_page_index = self.get_user_leave_action(plat_state)

            page_index = 1  # record page_index

            while page_index != leave_page_index + 1:
                # get engine action from user with request
                plat_action = self.EnginePolicy(plat_state)

                # concat platform state and action
                plat_state_action = torch.cat([plat_state, plat_action], dim=1)

                # customer state --> (plat_state, plat_action, page_index)
                state = torch.cat([plat_state_action, FLOAT([[page_index]]).to(device)], dim=1)
                action, _ = self.get_user_action(state)
                mask = 1 if leave_page_index == page_index else 0

                # add to memory
                self.memory.push(state.detach().cpu().numpy(), action.detach().cpu().numpy(), mask)
                page_index += 1

            num_items += leave_page_index

    def sample_batch(self, batch_size):
        """
        sample batch generate (state, action) pairs with mask.
        :param batch_size: mini_batch for update Discriminator
        :return: batch_gen, batch_mask
        """
        # sample batch (state, action) pairs from memory
        batch = self.memory.sample(batch_size)

        batch_state = FLOAT(np.stack(batch.state)).squeeze(1).to(device)
        batch_action = LONG(np.stack(batch.action)).to(device)
        batch_mask = INT(np.stack(batch.mask)).to(device)

        assert batch_state.size(0) == batch_size, "Expected batch size (s,a) pairs"

        return batch_state, batch_action, batch_mask

    def get_log_prob(self, user_state, user_action):
        _, action_prob = self.get_user_action(user_state)
        current_action_prob = action_prob.gather(1, user_action)

        return torch.log(current_action_prob.unsqueeze(1))
