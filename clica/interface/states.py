from __future__ import annotations
import copy
import curses
import os
import random
import string
import traceback
from typing import TYPE_CHECKING

from clica.interface.base_state import CLIState
from clica.interface.curses_utils import get_text_input, LineWriter, select_from_list, select_multiple_from_list
from clica.interface.inputs import *
from clica.database import ActionSource, ActionType
from clica.eval import run_agent_eval
from clica.problem_generation import generate_problem
from clica.solution_generation import generate_solution, get_actions_from_diff


if TYPE_CHECKING:
    from clica.interface.cli import InteractiveCLI


class PromptState(CLIState):
    state_id: str = 'PROMPT'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles user input in the prompt editor."""
        PromptState._render(cli)
        curses.echo()
        user_prompt = cli.stdscr.getstr(1, 0).decode('utf-8')
        curses.noecho()
        
        prompt_ids = cli.agent.tokenizer.encode(user_prompt, add_special_tokens=False)
        cli.env.set_instruction(prompt_ids)
        if cli._use_db:
            cli.db.add_action(user_prompt, ActionType.SET_INSTRUCTION, source=ActionSource.HUMAN)

        return MenuState

    @staticmethod
    def _render(cli: InteractiveCLI):
        """Renders the prompt editor to the terminal."""
        cli.stdscr.clear()
        writer = LineWriter(cli.stdscr)
        writer.write("Write a new prompt:", curses.A_BOLD)
        cli.stdscr.refresh()

    @staticmethod
    def _get_available_commands() -> dict[str, str]:
        return {
            'ESC': '[ESC] menu'
        }


class AutoPromptState(CLIState):
    state_id: str = 'AUTO_PROMPT'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Automatically generates a prompt for the user."""
        AutoPromptState._render(cli)
        
        user_prompt = generate_problem()
        
        prompt_ids = cli.agent.tokenizer.encode(user_prompt, add_special_tokens=False)
        cli.env.set_instruction(prompt_ids)
        if cli._use_db:
            cli.db.add_action(user_prompt, ActionType.SET_INSTRUCTION, source=ActionSource.AI)

        return MenuState

    @staticmethod
    def _render(cli: InteractiveCLI):
        pass


class ExampleState(CLIState):
    state_id: str = 'EXAMPLE'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles user input in the code editor."""
        cli.stdscr.keypad(False)
        cli.insert_queue = ''
        queued_actions = []
        render_queued = False

        while True:
            ExampleState._render(cli)
            
            # If there are actions in the queue, execute them and rerender the environment
            if len(queued_actions) > 0:
                action_id = queued_actions.pop(0)
                cli._env_enact(action_id, ActionSource.HUMAN)
                render_queued = True
                continue
            elif render_queued:
                ExampleState._render(cli)
                render_queued = False
            
            # Otherwise, get the next action from the user
            key = cli._query_and_parse_user_key()
            
            # Handle escape / resize events
            if key == '\x1b':
                # Enact all actions waiting in the queue before exiting
                if len(cli.insert_queue) > 0:
                    actions = cli.agent.tokenizer.encode(cli.insert_queue, add_special_tokens=False)
                    queued_actions.append(cli.agent.tokenizer.convert_tokens_to_ids(KEY_ENTER_TOKEN))
                    cli.insert_queue = ''
                    for act in actions:
                        cli._env_enact(act, ActionSource.HUMAN)
                break
            elif key == 'KEY_RESIZE':
                continue

            # If enter key, take actions for the queued text, and then take the enter key action
            if key == KEY_CTRL_RIGHT_TOKEN:
                queued_actions.extend(cli.agent.tokenizer.encode(cli.insert_queue, add_special_tokens=False))
                queued_actions.append(cli.agent.tokenizer.convert_tokens_to_ids(KEY_ENTER_TOKEN))
                cli.insert_queue = ''
                
            # Handle normal text
            elif key not in COMMAND_TOKENS:
                cli.insert_queue += key
            
            # Otherwise it is just a normal command token
            # If there is text queued, this will auto commit that queued text
            elif len(cli.insert_queue) > 0:
                queued_actions.extend(cli.agent.tokenizer.encode(cli.insert_queue, add_special_tokens=False))
                queued_actions.append(cli.agent.tokenizer.convert_tokens_to_ids(KEY_ENTER_TOKEN))
                queued_actions.append(cli.agent.tokenizer.convert_tokens_to_ids(key))
                cli.insert_queue = ''
            
            # Otherwise it is just a normal command token
            else:
                action_id = cli.agent.tokenizer.convert_tokens_to_ids(key)
                cli._env_enact(action_id, ActionSource.HUMAN)

        # self.agent.train_supervised(example_buffer)
        # Back to simplified key representations (e.g. 'KEY_UP')
        cli.stdscr.keypad(True)
        return MenuState
    
    @staticmethod
    def _get_available_commands() -> dict[str, str]:
        return {
            '^r': '[ctrl+r] run code',
            '^KEY_RIGHT': '[ctrl+right] insert text',
            'ESC': '[ESC] menu',
        }

    @staticmethod
    def _render(cli: InteractiveCLI):
        """Renders the project code to the terminal."""
        cli.stdscr.clear()
        writer = LineWriter(cli.stdscr)
        
        prompt_header = "=" * 5 + " User Instruction " + "=" * 5
        prompt_lines = cli._get_env_instruction().splitlines()
        
        code_header = "=" * 5 + " Project Code " + "=" * 5
        code = cli._get_env_code(include_cursor=True)

        text_queue_header = "=" * 5 + " Text Queue " + "=" * 5
        text_queue_lines = cli._get_env_text_queue().splitlines()
        
        prior_code, post_code = code.split(CURSOR_TOKEN, 1)
        prior_code = prior_code + cli.insert_queue

        exec_output_header = "=" * 5 + " Execution Output " + "=" * 5
        exec_output_lines = cli._get_env_exec_output().splitlines()

        writer.write(prompt_header, curses.A_ITALIC)
        writer.write(prompt_lines)
        writer.skip_lines(2)
        
        writer.write(code_header, curses.A_ITALIC)
        writer.write(prior_code, new_line=False)
        cursor_y, cursor_x = cli.stdscr.getyx()
        writer.write(post_code)
        writer.skip_lines(2)

        writer.write(text_queue_header, curses.A_ITALIC)
        writer.write(text_queue_lines)
        writer.skip_lines(2)

        writer.write(exec_output_header, curses.A_ITALIC)
        writer.write(exec_output_lines)
        
        writer.skip_lines(3)
        
        cli._write_command_menu_to_screen(writer)
    
        cli.stdscr.move(cursor_y, cursor_x)
        cli.stdscr.refresh()


class AutoSolveState(CLIState):
    state_id: str = 'AUTO_SOLVE'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Automatically generates an example solution for the current problem."""
        AutoSolveState._render(cli)

        instruction = cli._get_env_instruction()
        if not instruction.strip():
            return MenuState

        code = cli._get_env_code()
        exec_output = cli._get_env_exec_output()

        solution = generate_solution(instruction, code, exec_output)

        solution_ids = cli.agent.tokenizer.encode(solution, add_special_tokens=False)
        current_ids = cli.env.get_dict_obs(include_cursor=False)['code']
        actions = get_actions_from_diff(current_ids, solution_ids, cli.agent.tokenizer, cli.env._cursor_pos)

        for action in actions:
            cli._env_enact(action, ActionSource.AI)
            AutoSolveState._render(cli)

        return MenuState

    @staticmethod
    def _render(cli: InteractiveCLI):
        """Renders the auto example screen to the terminal."""
        cli.stdscr.clear()
        writer = LineWriter(cli.stdscr)
        cli._write_obs_to_screen(writer)
        cli.stdscr.refresh()


class ExitState(CLIState):
    state_id: str = 'EXIT'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        return None

    @staticmethod
    def _render(cli: InteractiveCLI):
        pass


class AgentTurnState(CLIState):
    state_id: str = 'AGENT_TURN'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles the agent's turn, prompting it to generate text."""
        action = None
        recurrent_state = None
        act_idx = 0
        while act_idx < cli.agent.max_gen_length:
            AgentTurnState._render(cli)
            recurrent_state, action = cli.agent.get_action(recurrent_state, cli.env.get_obs())
            cli._env_enact(action, ActionSource.AI)
            act_idx += 1
            if action == cli.agent.eos_token:
                break

        return MenuState

    @staticmethod
    def _render(cli: InteractiveCLI):
        MenuState._render(cli)


class TrainState(CLIState):
    state_id: str = 'TRAIN'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles training the agent."""
        cli.stdscr.clear()
        cli.stdscr.refresh()

        TrainState.train_agent(cli)

        print()
        print('=' * 40)
        print()
        print("Training complete, press any key to return to the menu...")

        cli.stdscr.getkey()
        return MenuState
        
    @staticmethod
    def train_agent(cli: InteractiveCLI):
        """
        Trains the agent using actions since the last training session.
        """
        actions = cli.db.get_actions_since_action_id(cli.last_trained_step)
        if not actions:
            return

        # Create a copy of the environment state from the last training session
        env = cli.last_trained_env
    
        # Train the agent
        cli.last_trained_env = cli.agent.train_on_actions(env, actions)

        # Update the last trained action ID and environment state
        cli.last_trained_step = actions[-1][0]
        cli.last_trained_env_state = copy.deepcopy(cli.env)
        
    @staticmethod
    def _render(cli: InteractiveCLI):
        pass


class SaveModelState(CLIState):
    state_id: str = 'SAVE_MODEL'

    @staticmethod
    def _generate_default_model_name():
        """Generates a default model name."""
        random_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"model_{random_id}"

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles saving the model."""
        cli.stdscr.clear()

        # A save directory must be set in the config to save a model
        if not cli.model_save_dir:
            cli.stdscr.addstr(0, 0, "Error: model_save_dir not set in config")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState
        
        writer = LineWriter(cli.stdscr)
        prompt = "Enter model name to save: "
        writer.write(prompt, curses.A_BOLD)
        writer.skip_lines(1)
        writer.write("[ENTER] save | [ESC] cancel")
        
        default_name = cli.loaded_model_name or SaveModelState._generate_default_model_name()
        text_window = cli.stdscr.subwin(1, 40, 0, len(prompt))

        cli.stdscr.refresh()

        model_name = get_text_input(text_window, default_name)

        # If escape key is pressed, return to menu
        if model_name is None:
            return MenuState

        cli.stdscr.clear()

        if not model_name:
            cli.stdscr.addstr(0, 0, "Error: Model needs a name to save")
            cli.stdscr.addstr(2, 0, "Press any key to try again...")
            cli.stdscr.getch()
            return SaveModelState

        full_path = os.path.join(cli.model_save_dir, model_name)
        cli.agent.save(full_path)
        cli.loaded_model_name = model_name
        cli.stdscr.addstr(0, 0, f"Model saved to: {full_path}")

        cli.stdscr.addstr(2, 0, "Press any key to continue...")
        cli.stdscr.getch()

        return MenuState


class LoadModelState(CLIState):
    state_id: str = 'LOAD_MODEL'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles loading one or more saved models."""
        if not cli.model_save_dir:
            cli.stdscr.clear()
            cli.stdscr.addstr(0, 0, "Error: model_save_dir not set in config")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState

        model_files = [f for f in os.listdir(cli.model_save_dir) if os.path.isdir(os.path.join(cli.model_save_dir, f))]
        if not model_files:
            cli.stdscr.clear()
            cli.stdscr.addstr(0, 0, "No saved models found.")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState

        selected_models = select_from_list(cli.stdscr, model_files, 'Select model to load:')

        if not selected_models:
            return MenuState

        for selected_model in selected_models:
            full_path = os.path.join(cli.model_save_dir, selected_model)
            
            cli.stdscr.clear()
            cli.stdscr.addstr(0, 0, f"Loading model '{selected_model}'...")
            cli.stdscr.refresh()
            
            try:
                cli.agent.load(full_path)
                cli.loaded_model_name = selected_model  # Note: This will only keep the last loaded model name
            except Exception as e:
                cli.stdscr.clear()
                cli.stdscr.addstr(0, 0, f"Error loading model '{selected_model}': {str(e)}")
                cli.stdscr.addstr(2, 0, "Press any key to continue...")
                cli.stdscr.refresh()
                cli.stdscr.getch()
                continue  # Continue to the next model if there's an error

        cli.stdscr.clear()
        if len(selected_models) == 1:
            cli.stdscr.addstr(0, 0, f"Model '{selected_models[0]}' loaded!")
        else:
            cli.stdscr.addstr(0, 0, f"{len(selected_models)} models loaded!")
        cli.stdscr.addstr(2, 0, "Press any key to continue...")
        cli.stdscr.refresh()

        cli.stdscr.getch()
        return MenuState


class ResetSessionState(CLIState):
    state_id: str = 'RESET_SESSION'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Resets the session, wiping the environment and starting a new session."""
        cli.stdscr.clear()
        writer = LineWriter(cli.stdscr)
        writer.write("Resetting session...", curses.A_BOLD)
        cli.stdscr.refresh()
        cli.reset_session()
        return MenuState


class EvalState(CLIState):
    state_id: str = 'EVAL'

    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles the evaluation process."""
        eval_data_path = cli.eval_data_path
        cli.stdscr.clear()
        
        if not eval_data_path:
            cli.stdscr.addstr(0, 0, "Error: eval_data_path not set in config")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState
        
        if not os.path.exists(eval_data_path):
            cli.stdscr.addstr(0, 0, f"Error: Invalid eval_data_path in config: {eval_data_path}")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState

        # Get list of eval items
        eval_items = [
            f for f in os.listdir(eval_data_path)
            if os.path.exists(os.path.join(eval_data_path, f))
        ]
        
        if not eval_items:
            cli.stdscr.addstr(0, 0, "No eval options found.")
            cli.stdscr.addstr(2, 0, "Press any key to continue...")
            cli.stdscr.refresh()
            cli.stdscr.getch()
            return MenuState

        selected_items = select_multiple_from_list(cli.stdscr, eval_items, 'Select evals to run:')

        if not selected_items:
            return MenuState

        # Reset to shell mode so eval output is printed to terminal normally
        cli.stdscr.refresh()
        curses.savetty()
        curses.reset_shell_mode()

        # Run evaluations
        results = {}
        for item in selected_items:
            # Clear the terminal and print the name of the eval
            os.system('cls' if os.name == 'nt' else 'clear')
            print(f"\nRunning evaluation: {item}\n")
            print("=" * 40)

            try:
                item_path = os.path.join(eval_data_path, item)
                result = run_agent_eval(cli.agent, item_path)
                results[item] = result
            except Exception as e:
                stack_trace = traceback.format_exc()
                results[item] = {
                    "error": str(e),
                    "stack_trace": stack_trace
                }

        curses.resetty()

        # Display results
        cli.stdscr.clear()
        writer = LineWriter(cli.stdscr)
        for item, result in results.items():
            writer.write(f"=== {item} ===", curses.A_BOLD)
            for metric, value in result.items():
                writer.write(f"{metric}: {value}")
            writer.skip_lines(1)

        writer.write("Press any key to continue...")
        cli.stdscr.refresh()
        cli.stdscr.getch()

        return MenuState


class MenuState(CLIState):
    state_id: str = 'MENU'
    key_to_state = {
        'p': PromptState,
        KEY_CTRL_P: AutoPromptState,
        ',': AutoPromptState,
        'e': ExampleState,
        't': TrainState,
        's': SaveModelState,
        'l': LoadModelState,
        '\n': AgentTurnState,
        '\r': AgentTurnState,
        KEY_CTRL_E: AutoSolveState,
        '.': AutoSolveState,
        '\x1b': ExitState,
        '\x03': ExitState,
        'r': ResetSessionState,
        'v': EvalState,  # Add this line
    }
    
    @staticmethod
    def handle_execution(cli: InteractiveCLI) -> CLIState:
        """Handles user input in the main menu."""
        MenuState._render(cli)
        key = cli.stdscr.getkey()
        
        state = None
        if key in MenuState.key_to_state:
            state = MenuState.key_to_state[key]
        elif key in ('+', '='):
            cli.curr_reward += 1
        elif key == '-':
            cli.curr_reward -= 1
        return state or MenuState

    @staticmethod
    def _render(cli: InteractiveCLI):
        """Renders the main menu to the terminal."""
        # Write to screen
        cli.stdscr.clear()

        writer = LineWriter(cli.stdscr)
        
        ### Env observation ###
        cli._write_obs_to_screen(writer)
        writer.skip_lines(3)

        ### Commands text ###
        cli._write_command_menu_to_screen(writer)
        writer.skip_lines(2)

        writer.write(f'Current reward: {cli.curr_reward}')

        cli.stdscr.refresh()
        
    @staticmethod
    def _get_available_commands() -> dict[str, str]:
        return {
            'p': '[p]rompt',
            'e': '[e]xample',
            't': '[t]rain',
            's': '[s]ave model',
            'l': '[l]oad model',
            '+': '[+] reward',
            '-': '[-] reward',
            'ENTER': '[ENTER] end turn',
            'r': '[r]eset session',
            'v': 'e[v]aluate',  # Add this line
        }




