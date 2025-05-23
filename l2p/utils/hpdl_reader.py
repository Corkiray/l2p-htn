import typing
from collections import OrderedDict
from fractions import Fraction
from itertools import product
from typing import Callable, Dict, List, Tuple, Union, cast

import pyparsing
import unified_planning as up
import unified_planning.model.htn as htn
import unified_planning.model.walkers
from unified_planning import model
from unified_planning.environment import Environment, get_env
from unified_planning.exceptions import UPUsageError
from unified_planning.model import FNode, expression, problem, Bind
from unified_planning.model.expression import Expression
from unified_planning.model.types import is_compatible_type

from datetime import datetime

if pyparsing.__version__ < "3.0.0":
    from pyparsing import ParseResults
else:
    from pyparsing.results import ParseResults

import pyparsing
from pyparsing import (
    Group,
    Keyword,
    OneOrMore,
    Optional,
    QuotedString,
    SkipTo,
    Suppress,
    Word,
    ZeroOrMore,
    alphanums,
    alphas,
    nestedExpr,
    restOfLine,
)

if pyparsing.__version__ < "3.0.0":
    from pyparsing import oneOf as one_of
else:
    from pyparsing import one_of


class HPDLGrammar:
    def __init__(self):
        name = Word(alphas, alphanums + "_" + "-")

        name_list = Group(Group(OneOrMore(name)) + Optional(Suppress("-") + name))

        variable = Optional(Suppress("?")) + name  # Optional for goals
        typed_variables = Group(
            Group(OneOrMore(variable)).setResultsName("variables")
            + Optional(Suppress("-") + name).setResultsName("type")
        )

        # Group of one or more variable and optionally their type
        parameters = Group(ZeroOrMore(typed_variables)).setResultsName("params")

        # Any predicate with/without parameters
        predicate = (
            Suppress("(")
            + Group(name.setResultsName("name") + parameters)
            + Suppress(")")
        )

        # ----------------------------------------------------------
        # Sections
        sec_requirements = (
            Suppress("(")
            + ":requirements"
            + OneOrMore(
                one_of(
                    ":strips :typing :negative-preconditions :disjunctive-preconditions :disjuntive-preconditions :equality :existential-preconditions :universal-preconditions :quantified-preconditions :conditional-effects :fluents :numeric-fluents :adl :durative-actions :duration-inequalities :timed-initial-literals :action-costs :hierarchy :htn-expansion :metatags :derived-predicates :negative-preconditions"
                )
            )
            + Suppress(")")
        )

        sec_types = (
            Suppress("(")
            + ":types"
            + OneOrMore(name_list).setResultsName("types")
            + Suppress(")")
        )

        # Same as sec_types
        sec_constants = (
            Suppress("(")
            + ":constants"
            + ZeroOrMore(name_list).setResultsName("constants")
            + Suppress(")")
        )

        sec_predicates = (
            Suppress("(")
            + ":predicates"
            + Group(OneOrMore(predicate)).setResultsName("predicates")
            + Suppress(")")
        )

        # Fails if curly brackets inside python_function, but as far as I know they
        # are only used in dictionaries who were introduced in Python 3.7, which
        # SIADEX does not support
        python_function = (
            Suppress("{") + SkipTo("}") + Suppress("}")
        )
        # Functions can specify -number type
        sec_functions = (
            Suppress("(")
            + ":functions"
            + Group(
                OneOrMore(
                    Group(
                        predicate
                        + Optional(Suppress("- number"))
                        + Optional(python_function.setResultsName("code"))
                    )
                )
            ).setResultsName("functions")
            + Suppress(")")
        )

        # derived evaluates a logical expression with given arguments
        # planner changes occurrences of "pre" with "exp"
        derived = Group(
            Suppress("(")
            + ":derived"
            + predicate
            + nestedExpr().setResultsName("exp")
            + Suppress(")")
        )

        # ----------------------------------------------------------
        # Actions
        action = Group(
            Suppress("(")
            + ":action"
            + name.setResultsName("name")
            + ":parameters"
            + Suppress("(")
            + parameters
            + Suppress(")")
            + Optional(":precondition" + nestedExpr().setResultsName("pre"))
            + Optional(":effect" + nestedExpr().setResultsName("eff"))
            + Suppress(")")
        )

        dur_action = Group(
            Suppress("(")
            + ":durative-action"
            + name.setResultsName("name")
            + ":parameters"
            + Suppress("(")
            + parameters
            + Suppress(")")
            + ":duration"
            + nestedExpr().setResultsName("duration")
            + ":condition"
            + nestedExpr().setResultsName("pre")  # CHANGED: PDDLReader uses "cond"
            + ":effect"
            + nestedExpr().setResultsName("eff")
            + Suppress(")")
        )

        # ----------------------------------------------------------
        # HPDL

        inline_def = Group(
            Suppress("(")
            + ":inline"
            + nestedExpr().setResultsName("cond")
            + nestedExpr().setResultsName("eff")
            + Suppress(")")
        ).setResultsName("inline")

        tag_def = Group(
            Suppress("(") + ":tag" + "prettyprint" + QuotedString('"') + Suppress(")")
        ).setResultsName("inline")

        # ----------------------------------------------------------
        # HTN

        subtask_def = Group(
            Suppress("(") + name.setResultsName("name") + parameters + Suppress(")")
        ).setResultsName("subtask")

        time_rest_subt = Group(
            Suppress("(")
            + nestedExpr().setResultsName("time_exp")
            + subtask_def
            + Suppress(")")
        )

        method = Group(
            Suppress("(")
            + ":method"
            + name.setResultsName("name")
            + ":precondition"
            + nestedExpr().setResultsName("pre")
            + Optional(":meta" + Suppress("(") + OneOrMore(tag_def) + Suppress(")"))
            + ":tasks"
            + Suppress("(")
            + Group(
                ZeroOrMore(
                    Group(
                        # Ordering is defined with parallel [] or sequential in absence
                        # Can't use () as it will collide with time constraints
                        Optional("[", default="(").setResultsName("ordering")
                        + OneOrMore(inline_def | subtask_def | time_rest_subt)
                        + Suppress(Optional("]"))
                    )
                )
            ).setResultsName("subtasks")
            + Suppress(")")
            + Suppress(")")
        )

        task = Group(
            Suppress("(")
            + ":task"
            + name.setResultsName("name")
            + ":parameters"
            + Suppress("(")
            + parameters
            + Suppress(")")
            + Group(OneOrMore(method)).setResultsName("methods")
            + Suppress(")")
        )

        # ----------------------------------------------------------

        domain = (
            Suppress("(")
            + "define"
            + Suppress("(")
            + "domain"
            + name.setResultsName("name")
            + Suppress(")")
            + Optional(sec_requirements).setResultsName("features")
            + Optional(sec_types)
            + Optional(sec_constants)
            + Optional(sec_predicates)
            + Optional(sec_functions)
            + Group(ZeroOrMore(derived)).setResultsName("derived")
            + Group(ZeroOrMore(task)).setResultsName("tasks")
            + Group(ZeroOrMore(action | dur_action)).setResultsName("actions")
            + Suppress(")")
        )

        # ----------------------------------------------------------
        # Problem

        objects = OneOrMore(
            Group(Group(OneOrMore(name)) + Optional(Suppress("-") + name))
        ).setResultsName("objects")

        metric = (Keyword("minimize") | Keyword("maximize")).setResultsName(
            "optimization"
        ) + (name | nestedExpr()).setResultsName("metric")

        goal = Group(
            Suppress("(")
            + ":tasks-goal"
            + ":tasks"
            + Suppress("(")
            + Group(
                ZeroOrMore(
                    Group(
                        # Ordering is defined with parallel [] or sequential ()
                        Optional("[", default="(").setResultsName("ordering")
                        + OneOrMore(subtask_def)
                        + Suppress(Optional("]"))
                    )
                )
            ).setResultsName("subtasks")
            + Suppress(")")
            + Suppress(")")
        )

        # Time customization
        sec_customization = Group(
            Suppress("(")
            + ":customization"
            + OneOrMore(nestedExpr())
            + Suppress(")")
        )

        # ----------------------------------------------------------

        problem = (
            Suppress("(")
            + "define"
            + Suppress("(")
            + "problem"
            + name.setResultsName("name")
            + Suppress(")")
            + Suppress("(")
            + ":domain"
            + name
            + Suppress(")")
            + Optional(sec_customization.setResultsName("customization"))
            + Optional(sec_requirements)
            + Optional(Suppress("(") + ":objects" + objects + Suppress(")"))
            + Suppress("(")
            + ":init"
            + ZeroOrMore(nestedExpr()).setResultsName("init")
            + Suppress(")")
            + Optional(
                goal.setResultsName("goal")
            )
            + Optional(Suppress("(") + ":metric" + metric + Suppress(")"))
            + Suppress(")")
        )

        # ----------------------------------------------------------

        domain.ignore(";" + restOfLine)
        problem.ignore(";" + restOfLine)

        self._domain = domain
        self._problem = problem
        self._parameters = parameters

    @property
    def domain(self):
        return self._domain

    @property
    def problem(self):
        return self._problem

    @property
    def parameters(self):
        return self._parameters


class HPDLReader:
    """
    Parse a `HPDL` domain file and, optionally, a `HPDL` problem file and generate the equivalent :class:`~unified_planning.model.Problem`.
    """

    def __init__(self, env: typing.Optional[Environment] = None):
        self._env = get_env(env)
        self._em = self._env.expression_manager
        self._tm = self._env.type_manager
        self._operators: Dict[str, Callable] = {
            "and": self._em.And,
            "or": self._em.Or,
            "not": self._em.Not,
            "imply": self._em.Implies,
            ">=": self._em.GE,
            "<=": self._em.LE,
            ">": self._em.GT,
            "<": self._em.LT,
            "=": self._em.Equals,
            "+": self._em.Plus,
            "-": self._em.Minus,
            "/": self._em.Div,
            "*": self._em.Times,
        }
        grammar = HPDLGrammar()
        self._pp_domain = grammar.domain
        self._pp_problem = grammar.problem
        self._pp_parameters = grammar.parameters
        self._fve = self._env.free_vars_extractor
        self._totalcost: typing.Optional[model.FNode] = None

        self.problem: model.Problem = None
        self.types_map: Dict[str, "model.Type"] = {}
        self.object_type_needed: bool = False
        self.universal_assignments: Dict["model.Action", List[ParseResults]] = {}
        self.has_actions_cost: bool = False

        self.inline_version = 0  # inline id counter
        self.derived = {}  # Parsed derived Dict[str, List[FNode]]

    # Parses sub_expressions and calls add_effect
    def _add_effect(
        self,
        act: Union[model.InstantaneousAction, model.DurativeAction],
        types_map: Dict[str, model.Type],
        universal_assignments: typing.Optional[
            Dict["model.Action", List[ParseResults]]
        ],
        exp: Union[ParseResults, str],
        cond: Union[model.FNode, bool] = True,
        timing: typing.Optional[model.Timing] = None,
        assignments: Dict[str, "model.Object"] = {},
    ):
        to_add = [(exp, cond)]
        while to_add:
            exp, cond = to_add.pop(0)
            if len(exp) == 0:
                continue  # ignore the case where the effect list is empty, e.g., `:effect ()`
            op = exp[0]
            if op == "and":
                exp = exp[1:]
                for e in exp:
                    to_add.append((e, cond))
            elif op == "when":
                cond = self._parse_exp({}, exp[1], assignments, types_map)
                to_add.append((exp[2], cond))
            elif op == "not":
                exp = exp[1]
                eff = (
                    self._parse_exp({}, exp, assignments, types_map),
                    self._em.FALSE(),
                    cond,
                )
                act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            elif op == "assign":
                eff = (
                    self._parse_exp({}, exp[1], assignments, types_map),
                    self._parse_exp({}, exp[2], assignments, types_map),
                    cond,
                )
                act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore
                # act.add_effect(timing, exp[0], exp[1], exp[2])
            elif op == "increase":
                eff = (
                    self._parse_exp({}, exp[1], assignments, types_map),
                    self._parse_exp({}, exp[2], assignments, types_map),
                    cond,
                )
                act.add_increase_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            elif op == "decrease":
                eff = (
                    self._parse_exp({}, exp[1], assignments, types_map),
                    self._parse_exp({}, exp[2], assignments, types_map),
                    cond,
                )
                act.add_decrease_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            elif op == "forall":
                assert isinstance(exp, ParseResults)
                # Get the list of universal_assignments linked to this action. If it does not exist, default it to the empty list
                assert universal_assignments is not None
                action_assignments = universal_assignments.setdefault(act, [])
                action_assignments.append(exp)
                # TODO: Add a Forall
                # eff = (
                #     self._parse_exp({}, exp, assignments, types_map),
                #     self._em.TRUE(),
                #     cond,
                # )
                # act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            else:
                eff = (
                    self._parse_exp({}, exp, assignments, types_map),
                    self._em.TRUE(),
                    cond,
                )
                act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore

    # Checks (at start/end/overall) and calls _add_effect
    def _parse_condition(
        self,
        exp: Union[ParseResults, str],
        types_map: Dict[str, model.Type],
        vars: typing.Optional[Dict[str, model.Variable]] = None,
    ):
        to_add = [(exp, vars)]
        res = []
        while to_add:
            exp, vars = to_add.pop(0)
            op = exp[0]
            if op == "and":
                for e in exp[1:]:
                    to_add.append((e, vars))
            elif op in self.derived:  # Check derived
                header, derived, params = self.derived[op]

                # Change content of derived with the appropriate variables of exp
                changed = self._change_derived(
                    derived, header, exp[1:]
                )  # exp[0] is the derived name

                # Get changed exp as FNode
                fluent = self._parse_exp({}, changed[0], {}, params)

                res.append((model.StartTiming(), fluent))
            elif op == "forall":  # TODO: Why don't we use _em.Forall
                vars_string = " ".join(exp[1])
                vars_res = self._pp_parameters.parseString(vars_string)
                if vars is None:
                    vars = {}

                for g in vars_res["params"]:
                    # TODO: Check this
                    # t = types_map[g[1] if len(g) > 1 else "object"]
                    t = self.types_map[g[1] if len(g) > 1 else "object"]
                    for o in g[0]:
                        vars[o] = up.model.Variable(o, t, self._env)
                to_add.append((exp[2], vars))
            elif len(exp) == 3 and op == "at" and exp[1] == "start":
                cond = self._parse_exp(
                    {} if vars is None else vars, exp[2], {}, types_map
                )
                if vars is not None:
                    cond = self._em.Forall(cond, *vars.values())
                res.append((model.StartTiming(), cond))
            elif len(exp) == 3 and op == "at" and exp[1] == "end":
                cond = self._parse_exp(
                    {} if vars is None else vars, exp[2], {}, types_map
                )
                if vars is not None:
                    cond = self._em.Forall(cond, *vars.values())
                res.append((model.EndTiming(), cond))
            elif len(exp) == 3 and op == "over" and exp[1] == "all":
                t_all = model.OpenTimeInterval(model.StartTiming(), model.EndTiming())
                cond = self._parse_exp(
                    {} if vars is None else vars, exp[2], {}, types_map
                )
                if vars is not None:
                    cond = self._em.Forall(cond, *vars.values())
                res.append((t_all, cond))
            else:  # HPDL accept any exp, and considers (at start ...)
                # vars = {} if vars is None else vars
                cond = self._parse_exp({} if vars is None else vars, exp, {}, types_map)
                # cond = self._parse_exp({} if vars is None else vars, exp, {}, types_map)
                if vars is not None:
                    cond = self._em.Forall(cond, *vars.values())
                res.append((model.StartTiming(), cond))

        return res

    # Checks (at start/end/overall) and calls _add_effect
    def _add_timed_effects(
        self,
        act: model.DurativeAction,
        types_map: Dict[str, model.Type],
        universal_assignments: typing.Optional[
            Dict["model.Action", List[ParseResults]]
        ],
        eff: ParseResults,
        assignments: Dict[str, "model.Object"] = {},
    ):
        to_add = [eff]
        while to_add:
            eff = to_add.pop(0)
            op = eff[0]
            if op == "and":
                for e in eff[1:]:
                    to_add.append(e)
            elif len(eff) == 3 and op == "at" and eff[1] == "start":
                self._add_effect(
                    act,
                    types_map,
                    universal_assignments,
                    eff[2],
                    timing=model.StartTiming(),
                    assignments=assignments,
                )
            elif len(eff) == 3 and op == "at" and eff[1] == "end":
                self._add_effect(
                    act,
                    types_map,
                    universal_assignments,
                    eff[2],
                    timing=model.EndTiming(),
                    assignments=assignments,
                )
            elif len(eff) == 3 and op == "forall":
                assert universal_assignments is not None
                action_assignments = universal_assignments.setdefault(act, [])
                action_assignments.append(eff)
                # TODO: Use Forall
                # self._add_effect(
                #     act,
                #     types_map,
                #     universal_assignments,
                #     eff,  # CHANGED
                #     timing=model.EndTiming(),
                #     assignments=assignments,
                # )
            else:  # HPDL accept any exp, and considers (at end ...)
                self._add_effect(
                    act,
                    types_map,
                    universal_assignments,
                    eff,  # CHANGED
                    timing=model.EndTiming(),
                    assignments=assignments,
                )

    def _check_if_object_type_is_needed(self, domain_res) -> bool:
        """In HPDL we can declare variables outside the parameters section, so
        if we try the same philosophy as in pddl_reader, we will need to check
        almost all the domain, and it's not feasible to do it here, but rather
        while building the problem object"""
        return True

    def _durative_action_has_cost(self, dur_act: model.DurativeAction):
        if self._totalcost in self._fve.get(
            dur_act.duration.lower
        ) or self._totalcost in self._fve.get(dur_act.duration.upper):
            return False
        for _, cl in dur_act.conditions.items():
            for c in cl:
                if self._totalcost in self._fve.get(c):
                    return False
        for _, el in dur_act.effects.items():
            for e in el:
                if (
                    self._totalcost in self._fve.get(e.fluent)
                    or self._totalcost in self._fve.get(e.value)
                    or self._totalcost in self._fve.get(e.condition)
                ):
                    return False
        return True

    def _instantaneous_action_has_cost(self, act: model.InstantaneousAction):
        for c in act.preconditions:
            if self._totalcost in self._fve.get(c):
                return False
        for e in act.effects:
            if self._totalcost in self._fve.get(
                e.value
            ) or self._totalcost in self._fve.get(e.condition):
                return False
            if e.fluent == self._totalcost:
                if (
                    not e.is_increase()
                    or not e.condition.is_true()
                    or not (e.value.is_int_constant() or e.value.is_real_constant())
                ):
                    return False
        return True

    def _problem_has_actions_cost(self, problem: model.Problem):
        if (
            self._totalcost is None
            or not problem.initial_value(self._totalcost).constant_value() == 0
        ):
            return False
        for _, el in problem.timed_effects.items():
            for e in el:
                if (
                    self._totalcost in self._fve.get(e.fluent)
                    or self._totalcost in self._fve.get(e.value)
                    or self._totalcost in self._fve.get(e.condition)
                ):
                    return False
        for c in problem.goals:
            if self._totalcost in self._fve.get(c):
                return False
        return True

    def _build_problem(self, name: str, features: List[str]) -> model.Problem:
        features = set(features)
        if ":hierarchy" in features or ":htn-expansion" in features:
            return htn.HierarchicalProblem(
                name,
                self._env,
                initial_defaults={self._tm.BoolType(): self._em.FALSE()},
            )

        raise Exception("HPDLReader: Wrong reader, use PDDL instead")

    def _parse_types(self, types_list: List[str]):
        """Parses a type from the domain"""
        # types_list is a List of 1 or 2 elements, where the first one
        # is a List of types, and the second one can be their father,
        # if they have one.
        father: typing.Optional["model.Type"] = None
        if len(types_list) == 2:  # the types have a father
            if types_list[1] != "object":  # the father is not object
                father = self.types_map[types_list[1]]
            elif self.object_type_needed:  # the father is object, and object is needed
                object_type = self.types_map.get("object", None)
                if object_type is None:  # the type object is not defined
                    father = self._env.type_manager.UserType("object", None)
                    self.types_map["object"] = father
                else:
                    father = object_type
        else:
            assert (
                len(types_list) == 1
            ), "Malformed list of types, I was expecting either 1 or 2 elements"  # sanity check
        for type_name in types_list[0]:
            if type_name != "object":
                self.types_map[type_name] = self._env.type_manager.UserType(
                    type_name, father
                )

    def _parse_predicate(self, predicate: List[str]) -> model.Fluent:
        name = predicate[0]
        params = self._parse_params(predicate[1])
        return model.Fluent(name, self._tm.BoolType(), params, self._env)

    def _parse_params(self, params: List[str]) -> OrderedDict:
        res_params = OrderedDict()
        for g in params:
            param_type = self.types_map[g[1] if len(g) > 1 else "object"]
            for param_name in g[0]:
                res_params[param_name] = param_type
        return res_params

    def _parse_function(self, func: OrderedDict) -> model.Fluent:
        name = func[0][0]  # Modified due to added grammar group
        params = self._parse_params(func[0][1])

        # Check for python fluent
        code = func.get("code", None)
        if code is not None:
            # Fix no indentation in first line
            # Count spaces of next non-empty line and put add them to the first
            lines = [l for l in code[0].split("\n") if len(l) > 0]

            if len(lines) > 1:
                spaces = len(lines[1]) - len(lines[1].lstrip())
                lines[0] = " " * spaces + lines[0]

            code = "\n".join(lines)

            f = model.Fluent(name, self._tm.FuncType(), params, self._env, code)
        else:
            f = model.Fluent(name, self._tm.RealType(), params, self._env)

        if name == "total-cost":
            self.has_actions_cost = True
            self._totalcost = cast(model.FNode, self._em.FluentExp(f))
        return f

    def _parse_constant(self, constant: List[str]) -> List[model.Object]:
        o_type = self.types_map[constant[1] if len(constant) > 1 else "object"]
        objects: List[model.Object] = []
        for name in constant[0]:
            objects.append(model.Object(name, o_type, self._env))
        return objects

    def _build_task(self, task: OrderedDict) -> htn.Task:
        task_name = task["name"]

        task_params = self._parse_params(task.get("params", []))
        return htn.Task(task_name, task_params)

    def _parse_exp_str(
        self,
        var: Dict[str, model.Variable],
        exp: str,
        assignments: Dict[str, "model.Object"] = {},
        available_params: typing.Optional[
            OrderedDict[str, "model.Type"]
        ] = None,  # If we are parsing a precondition or effect, we need to know the valid parameters
    ) -> model.FNode:
        if exp[0] == "?" and exp[1:] in var:  # variable in a quantifier expression
            return self._em.VariableExp(var[exp[1:]])
        elif exp[0] in self.derived:  # Check derived
            header, derived, params = self.derived[exp[0]]

            # Change content of derived with the appropriate variables of exp
            changed = self._change_derived(
                derived, header, exp[1:]
            )  # exp[0] is the derived name

            # Get changed exp as FNode
            fluent = self._parse_exp({}, changed[0], {}, params)

            return fluent, None
        elif exp in assignments:  # quantified assignment variable
            return self._em.ObjectExp(assignments[exp])
        elif exp == '?dur': # Duration variable (TODO: Add ?start and ?end)
            return self._em.ParameterExp(
                model.Parameter(
                    exp[1:],
                    self._env.type_manager.RealType(), 
                    self._env
                )
            )
        elif exp[0] == "?":  # action parameter
            assert available_params is not None, "valid_params cannot be None"
            assert exp[1:] in available_params, f"Invalid parameter {exp[1:]}"

            return self._em.ParameterExp(
                model.Parameter(exp[1:], available_params[exp[1:]], self._env)
            )
        elif self.problem.has_fluent(exp):  # fluent
            return self._em.FluentExp(self.problem.fluent(exp))
        elif self.problem.has_object(exp):  # object
            return self._em.ObjectExp(self.problem.object(exp))
        elif exp[0] == '"' or exp[0] == "'": # date
            raise TypeError("HPDL reader does not support dates appearing in the domain")
        else:  # number
            n = Fraction(exp)
            if n.denominator == 1:
                return self._em.Int(n.numerator)
            else:
                return self._em.Real(n)

    def _parse_exp_parse_result(
        self,
        var: Dict[str, model.Variable],
        exp: ParseResults,
        assignments: Dict[str, "model.Object"],
    ) -> Tuple[
        Union[model.FNode, None], Union[List[Tuple[typing.Any, typing.Any, bool]], None]
    ]:
        if len(exp) == 0:  # empty precondition
            return self._em.TRUE(), None
        elif exp[0] == "-" and len(exp) == 2:  # unary minus
            return None, [(var, exp, True), (var, exp[1], False)]
        elif exp[0] in self._operators:  # n-ary operators
            res = [(var, exp, True)]
            for e in exp[1:]:
                res.append((var, e, False))
            return None, res
        elif exp[0] in self.derived:  # Check derived and substitute
            header, derived, params = self.derived[exp[0]]

            # Change content of derived with the appropriate variables of exp
            changed = self._change_derived(
                derived, header, exp[1:]
            )  # exp[0] is the derived name

            # Get changed exp as FNode
            fluent = self._parse_exp({}, changed[0], {}, params)

            return fluent, None
        elif exp[0] in ["exists", "forall"]:  # quantifier operators
            vars_string = " ".join(exp[1])
            vars_res = self._pp_parameters.parseString(vars_string)
            vars = {}
            for g in vars_res["params"]:
                t = self.types_map[g[1] if len(g) > 1 else "object"]
                for o in g[0]:
                    vars[o] = model.Variable(o, t, self._env)
            # if exp[0] == "exists":
            #     return self._em.Exists(exp[2], *vars), None
            # else:
            #     return self._em.Forall(exp[2], *vars), None
            return None, [(vars, exp, True), (vars, exp[2], False)]
        elif self.problem.has_fluent(exp[0]):  # fluent reference
            res = [(var, exp, True)]
            for e in exp[1:]:
                # If type is received, exp declares an
                # object and is processed in build_method

                # No number type. has_type checks values (not keys) from types_map,
                # but number is real there
                if e != "-" and not self.problem.has_type(e) and not e == "number":
                    res.append((var, e, False))
            return None, res
        elif exp[0] in assignments:  # quantified assignment variable
            assert len(exp) == 1
            return None, [(var, exp, True)]
        elif len(exp) == 1:  # expand an element inside brackets
            return None, [(var, exp[0], False)]
        elif exp[0] == "bind" and len(exp) == 3: # bind assignment
            res = [(var, exp, True)]
            for e in exp[1:]:
                res.append((var, e, False))
            return None, res
        else:
            raise SyntaxError(f"Not able to handle: {exp}")

    def _parse_exp(
        self,
        var: Dict[str, model.Variable],
        exp: Union[ParseResults, str],
        assignments: Dict[str, "model.Object"] = {},
        available_params: typing.Optional[
            OrderedDict[str, "model.Type"]
        ] = None,  # If we are parsing a precondition or effect, we need to know the valid parameters
    ) -> model.FNode:
        stack = [(var, exp, False)]
        solved: List[model.FNode] = []
        while len(stack) > 0:
            var, exp, status = stack.pop()
            if status:
                if exp[0] == "-" and len(exp) == 2:  # unary minus
                    solved.append(self._em.Times(-1, solved.pop()))
                elif exp[0] in self._operators:  # n-ary operators
                    op: Callable = self._operators[exp[0]]
                    solved.append(op(*[solved.pop() for _ in exp[1:]]))
                elif exp[0] in ["exists", "forall"]:  # quantifier operators
                    q_op: Callable = (
                        self._em.Exists if exp[0] == "exists" else self._em.Forall
                    )
                    solved.append(q_op(solved.pop(), *var.values()))
                elif self.problem.has_fluent(exp[0]):  # fluent reference
                    f = self.problem.fluent(exp[0])
                    # In object is declared in the exp do not pop for the (- object)
                    args = [
                        solved.pop()
                        for e in exp[1:]
                        # No number type. has_type checks values (not keys) from types_map,
                        # but number is real there
                        if e != "-" and not self.problem.has_type(e) and not e == "number"
                    ]
                    solved.append(self._em.FluentExp(f, tuple(args)))
                elif exp[0] in assignments:  # quantified assignment variable
                    assert len(exp) == 1
                    solved.append(self._em.ObjectExp(assignments[exp[0]]))
                elif exp[0] == "bind": # bind assignment
                    assert len(exp) == 3
                    solved.append(Bind(*[solved.pop() for _ in exp[1:]]))
                else:
                    raise up.exceptions.UPUnreachableCodeError
            else:
                if isinstance(exp, ParseResults):
                    node, new_stack = self._parse_exp_parse_result(
                        var, exp, assignments
                    )
                    if node:
                        solved.append(node)
                    if new_stack:
                        stack.extend(new_stack)
                elif isinstance(exp, str):
                    # Instead of having to pass the action already built, we can pass the available parameters
                    node = self._parse_exp_str(var, exp, assignments, available_params)
                    solved.append(node)
                else:
                    raise SyntaxError(f"Not able to handle: {exp}")
        assert len(solved) == 1  # sanity check
        return solved.pop()

    def _parse_effect(
        self,
        exp: Union[ParseResults, str],
        cond: Union[model.FNode, bool] = True,
        timing: typing.Optional[model.Timing] = None,
        assignments: Dict[str, "model.Object"] = {},
        available_params: typing.Optional[OrderedDict[str, "model.Type"]] = None,
    ) -> List[Tuple[model.FNode, model.FNode, Union[model.FNode, bool], str]]:
        to_add = [(exp, cond)]
        result: List[
            Tuple[model.FNode, model.FNode, Union[model.FNode, bool], str]
        ] = []
        while to_add:
            exp, cond = to_add.pop(0)
            if len(exp) == 0:
                continue  # ignore the case where the effect list is empty, e.g., `:effect ()`
            op = exp[0]
            if op == "and":
                exp = exp[1:]
                for e in exp:
                    to_add.append((e, cond))
            elif op == "when":
                cond = self._parse_exp({}, exp[1], assignments, available_params)
                to_add.append((exp[2], cond))
            elif op == "not":
                exp = exp[1]
                eff = (
                    self._parse_exp({}, exp, assignments, available_params),
                    self._em.FALSE(),
                    cond,
                    op,
                )
                result.append(eff)
                # act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            elif op == "assign" or op == "increase" or op == "decrease":
                eff = (
                    self._parse_exp({}, exp[1], assignments, available_params),
                    self._parse_exp({}, exp[2], assignments, available_params),
                    cond,
                    op,
                )
                result.append(eff)
                # act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore
            elif op == "forall":
                assert isinstance(exp, ParseResults)
                # TODO: Clean
                # Get the list of universal_assignments linked to this action. If it does not exist, default it to the empty list
                # assert self.universal_assignments is not None
                # assert act is not None
                # action_assignments = self.universal_assignments.setdefault(act, [])
                # action_assignments.append(exp)

                # Returning and checking elsewhere
                # eff = (
                #     self._parse_exp({}, exp, assignments, available_params),
                #     self._em.TRUE(),
                #     cond,
                #     None,
                # )
                # result.append(eff)
                result.append(exp)
            else:
                eff = (
                    self._parse_exp({}, exp, assignments, available_params),
                    self._em.TRUE(),
                    cond,
                    None,
                )
                result.append(eff)
                # act.add_effect(*eff if timing is None else (timing, *eff))  # type: ignore

        return result

    def _build_durative_action(
        self,
        name: str,
        params: OrderedDict,
        duration: ParseResults,
        cond: Union[ParseResults, str],
        eff: ParseResults,
    ) -> model.DurativeAction:

        dur_act = model.DurativeAction(name, params, self._env)

        # Parse duration
        if duration[0] == "=":
            duration.pop(0)
            duration.pop(0)
            dur_act.set_fixed_duration(self._parse_exp({}, duration, {}, params))
        elif duration[0] == "and":
            upper = None
            lower = None
            for j in range(1, len(duration)):
                if duration[j][0] == ">=" and lower is None:
                    duration[j].pop(0)
                    duration[j].pop(0)
                    lower = self._parse_exp({}, duration[j], {}, params)
                elif duration[j][0] == "<=" and upper is None:
                    duration[j].pop(0)
                    duration[j].pop(0)
                    upper = self._parse_exp({}, duration[j], {}, params)
                else:
                    raise SyntaxError(
                        f"Not able to handle duration constraint of action {name}"
                    )
            if lower is None or upper is None:
                raise SyntaxError(
                    f"Not able to handle duration constraint of action {name}"
                )
            d = model.ClosedDurationInterval(lower, upper)
            dur_act.set_duration_constraint(d)
        else:
            raise SyntaxError(
                f"Not able to handle duration constraint of action {name}"
            )

        # Add conditions to action
        if cond: # If not empty
            conditions = self._parse_condition(cond, params)
            for c in conditions:
                dur_act.add_condition(c[0], c[1])

        # Add each effect to action
        self._add_timed_effects(dur_act, params, self.universal_assignments, eff)

        # Check action cost
        self.has_actions_cost = (
            self.has_actions_cost and self._durative_action_has_cost(dur_act)
        )

        return dur_act

    def _build_action(
        self,
        name: str,
        params: OrderedDict,
        pre: model.FNode = None,
        eff: List[Tuple[model.FNode, model.FNode, Union[model.FNode, bool], str]] = [],
        durative: bool = False,
        duration: typing.Optional[List[str]] = None,
    ) -> model.Action:

        # Durative actions
        if durative:
            return self._build_durative_action(name, params, duration[0], pre, eff)

        act = model.InstantaneousAction(name, params, self._env)

        if pre:
            act.add_precondition(pre)

        for f in eff:
            if f[0] == "forall":
                assert self.universal_assignments is not None
                action_assignments = self.universal_assignments.setdefault(act, [])
                action_assignments.append(f)
                # TODO: Use Forall
                # self._em.Forall(cond, *vars.values())
                # self._add_effect(act, {}, {}, f, {}, None, {})
                # act.add_effect(self._em.Forall(f), self._em.TRUE())
                # act.add_effect(f[0], f[1])
            elif f[3] == "assign":
                act.add_effect(f[0], f[1], f[2])
            elif f[3] == "increase":
                act.add_increase_effect(f[0], f[1], f[2])
            elif f[3] == "decrease":
                act.add_decrease_effect(f[0], f[1], f[2])
            else:
                act.add_effect(f[0], f[1], f[2])

        # self.problem.add_action(act)
        # Do we need to do it here? it comes from _parse_problem method
        self.has_actions_cost = (
            self.has_actions_cost and self._instantaneous_action_has_cost(act)
        )
        return act

    def _parse_action(
        self, action: OrderedDict
    ) -> Tuple[str, OrderedDict, bool, List[str], List[model.Fluent]]:
        """Parses an action from the domain and return the name and the parameters"""
        action_name = action["name"]
        a_params = self._parse_params(action["params"])
        durative = False
        duration = None

        if "duration" in action:
            durative = True
            duration = action["duration"]

        res = OrderedDict()

        res["name"] = action_name
        res["params"] = a_params
        res["durative"] = durative
        res["duration"] = duration

        if "pre" in action:
            res["pre"] = self._parse_exp({}, action["pre"][0], {}, a_params)
        else:  # "pre" always exist, although it might be empty
            res["pre"] = []

        if "eff" in action:
            res["eff"] = self._parse_effect(action["eff"][0], True, None, {}, a_params)
        else:
            res["eff"] = []

        return res

    def _parse_inline(
        self,
        inline,
        # method_name: str,
        method_params: OrderedDict,  # Task and pre params of method
    ) -> model.Action:
        inline_version = 0
        # inline_name = (method_name or "") + "_inline"
        inline_name = "inline"

        # Find the first available name for the inline task
        inline_version = self.inline_version
        self.inline_version += 1

        res = OrderedDict()

        res["name"] = f"{inline_name}_{inline_version}"
        res["durative"] = False
        res["duration"] = []

        # Check for params declared in inline
        cond_params = self._get_params_in_exp(inline["cond"][0])
        eff_params = self._get_params_in_exp(inline["eff"][0])

        # Join with method_params
        res["params"] = method_params
        res["params"].update(cond_params)
        res["params"].update(eff_params)
        res["cond"] = []
        res["eff"] = []

        # Parse conditions and effects
        if "cond" in inline:
            res["cond"] = self._parse_exp({}, inline["cond"][0], {}, res["params"])

        if "eff" in inline:
            res["eff"] = self._parse_effect(
                inline["eff"][0], True, None, {}, res["params"]
            )

        # Build action
        action_model = self._build_action(
            res["name"],
            res["params"],
            res["cond"],
            res["eff"],
            res["durative"],
            res["duration"],
        )

        # Add inline to the problem
        self.problem.add_action(action_model)

        # Return subtask
        return htn.Subtask(action_model, *action_model.parameters)

    # --------------------------------------------------------------------------
    # --------------------------------------------------------------------------

    def _get_params_in_exp(self, exp) -> OrderedDict:
        """Returns params found in exp"""
        params = OrderedDict()

        stack = [exp]
        while len(stack) > 0:
            exp = stack.pop()

            # Check for single strings
            # (probably won't return anything, as each string is only one word)
            if exp == "bind": # TODO: Improve
                var = stack.pop()
                if var[0] == "?":
                    params[var[1:]] = self.types_map["number"]
                else:
                    raise ValueError(f"Bind expression not well-defined")
            elif isinstance(exp, str):
                params.update(self._parse_params_and_types(exp))
            # elif len(exp) >= 2:
            else: # Accepting also list inside list
                # Check if all elements are strings
                if all(isinstance(e, str) for e in exp):
                    params.update(self._parse_params_and_types(exp))
                else:
                    # Find params in each sub expression
                    for e in reversed(exp): # From beginning to end (inverse stack)
                        stack.append(e)
        return params

    def _parse_params_and_types(self, params: List[str]) -> List[str]:
        """Parses a list of parameters and returns a list of the parameters names. Only returns declared parameters (?o - <type>)"""

        def parse_type(type: str):
            if type in self.types_map:
                return self.types_map[type]  # Must return the object, not the str
            else:
                raise ValueError(f"Type {type} not defined")

        res_params = OrderedDict()
        i = 0
        while i < len(params):
            if params[i][0] == "?":  # parameter
                if (
                    i + 1 < len(params) and params[i + 1] == "-"
                ):  # type is specified, check it
                    res_params[params[i][1:]] = parse_type(params[i + 2])
                    i += 3
                else:  # No type specified, ignore
                    # res_params[params[i][1:]] = self.types_map["object"]
                    # In case we need to get non-specified params,
                    # uncomment line above
                    i += 1

            else:  # Not a param, ignore
                i += 1

        return res_params

    # --------------------------------------------------------------------------
    # --------------------------------------------------------------------------

    def _build_task(self, task: OrderedDict) -> htn.Task:
        task_name = task["name"]

        task_params = self._parse_params(task.get("params", []))

        return htn.Task(task_name, task_params)

    def _add_time_const(self, exp, subtask, method_params):
        """Parse a time constraint applied to a subtask. Possible variables are
        ?start, ?end and ?dur. Constraints that affect ?start or ?end indicates
        time units from the planning start time (GlobalStart).

        Results read as:

        - duration = (7, global_end]

            duration must take more than 7 time units

        - end = [global_start + 40, global_end]

            end must happen 40 time units after the start of the plan
        """
        # Let _parse_exp know which are time variables
        time_params = {
            "start": self._tm.RealType(),
            "end": self._tm.RealType(),
            "duration": self._tm.RealType(),
        }
        parsed_exp = self._parse_exp({}, exp, {}, time_params | method_params)

        # Check for more than one exp
        sub_exp = parsed_exp.args if parsed_exp.is_and() else [parsed_exp]

        for e in sub_exp:
            v_id, r_id, less_than = (
                (0, 1, True) if e.arg(0).is_parameter_exp() else (1, 0, False)
            )

            var = e.arg(v_id).parameter().name  # Time variable affected (start/end/dur)
            restriction = e.arg(r_id)  # Constraint applied

            e_str = str(e)  # expression as string

            # Set range bounds of restriction
            if "start" in var:
                upper = (
                    model.GlobalStartTiming() if less_than else model.GlobalEndTiming()
                )
                lower = model.GlobalStartTiming(restriction)
            elif "end" in var:
                upper = (
                    model.GlobalStartTiming() if less_than else model.GlobalEndTiming()
                )
                lower = model.GlobalStartTiming(restriction)
            elif "duration" in var:
                if "<" in e_str:  # Includes <=
                    if less_than:  # ?var <= restriction
                        upper = model.GlobalStartTiming()
                        # lower = restriction # This should be Timing, not FNode
                        lower = model.GlobalStartTiming(restriction)
                    else:  # restriction <= ?var
                        upper = model.GlobalEndTiming()
                        # lower = restriction # This should be Timing, not FNode
                        lower = model.GlobalStartTiming(restriction)
                elif "==" in e_str:
                    # lower = restriction # This should be Timing, not FNode
                    lower = model.GlobalStartTiming(restriction)
                else:
                    raise SyntaxError(f"Not able to handle: {e_str}")
            else:
                raise SyntaxError(f"Not able to handle: {e}")

            # Get interval
            if "<=" in e_str:
                if less_than:  # ?var <= restriction
                    constraint = model.ClosedTimeInterval(upper, lower)
                else:  # restriction <= ?var
                    constraint = model.ClosedTimeInterval(lower, upper)
            elif "<" in e_str:
                if less_than:  # ?var < restriction
                    constraint = model.RightOpenTimeInterval(upper, lower)
                else:  # restriction < ?var
                    constraint = model.LeftOpenTimeInterval(lower, upper)
            elif "==" in e_str:
                constraint = model.ClosedTimeInterval(lower, lower)
                # CHECK: Maybe FixedDuration(lower)?
                # Above is ClosedTimeInterval for consistency with the rest of
                # TimeInterval
            else:
                raise SyntaxError(f"Not able to handle: {e_str}")

            # Add interval to subtask
            if "start" in var:
                subtask.set_start_constraint(constraint, less_than)
            elif "end" in var:
                subtask.set_end_constraint(constraint, less_than)
            elif "duration" in var:
                subtask.set_duration_constraint(constraint, less_than)

    def _parse_method(
        self,
        method: OrderedDict,
        method_params: OrderedDict,  # Task and pre params of method
    ):
        # Parse subtasks
        ordered_subtasks = []  # List of tuple (order, list(subtask))
        subtasks_params = []

        # Get ordered subtasks
        for ordering in method.get("subtasks", []):
            subtasks = []  # List of model.Subtask
            order = ordering.get("ordering", "(")

            # ordering[0] is the order tag, rest are subtasks definitions
            for subs in ordering[1:]:
                # TODO: Add time restrictions from branch time-constraints
                time = subs.get("time_exp", None)
                if time is not None:
                    subtask_model = self._parse_subtask(subs["subtask"], method_params)
                    self._add_time_const(
                        time, subtask_model, method_params
                    )  # Add time constraint
                else:
                    # TODO: Clean
                    subtask_model = self._parse_subtask(subs, method_params)

                if subtask_model is not None:
                    # Add model to list
                    subtasks.append(subtask_model)

                    # Get model.Parameter for each param
                    # TODO: See parse_inline params TODOs
                    for p in subtask_model.parameters:
                        subtasks_params.append(p.parameter())

            # Append ordering to method subtasks
            ordered_subtasks.append((order, subtasks))

        return ordered_subtasks, subtasks_params

    # self.problem must have task built
    def _build_method(self, method: OrderedDict, task_name: str) -> htn.Method:
        method_name = f'{task_name}-{method["name"]}'  # Methods names are
        # not unique across tasks

        method_params = OrderedDict()

        # Get parent task params
        task_model = self.problem.get_task(task_name)
        task_params = OrderedDict({p.name: p.type for p in task_model.parameters})
        method_params.update(task_params)

        # Get precondition params
        method_preconditions = method.get("pre", [])
        for pre in method_preconditions:
            pre_params = self._get_params_in_exp(pre)
            method_params.update(pre_params)

        # Parse and build method subtasks
        subtasks, params = self._parse_method(method, method_params)

        # Get subtasks params as OrderedDict
        subtask_params = OrderedDict({p.name: p.type for p in params})
        method_params.update(subtask_params)

        # ----------------------------
        # Build model
        method_model = htn.Method(method_name, method_params)

        # Add parent task to model
        method_model.set_task(task_model)

        # Add subtasks to model
        for ordering in subtasks:
            for s in ordering[1]:
                method_model.add_subtask(s)

            if ordering[0] == "(":
                method_model.set_ordered(*ordering[1])

        # All subtasks from the next iteration have sequential order with
        # respect to the previous iteration
        if len(subtasks) >= 2:
            for i in range(1, len(subtasks)):
                # Loop through subtasks of previous ordering
                for s1 in subtasks[i - 1][1]:
                    for s2 in subtasks[i][1]:
                        method_model.set_ordered(s1, s2)

        # Add preconditions to model
        for pre in method_preconditions:
            parsed_pre = self._parse_exp({}, pre, {}, method_params)
            method_model.add_precondition(parsed_pre)

        return method_model

    def _parse_subtask(
        self,
        subtask: OrderedDict,
        method_params: OrderedDict,  # Task and pre params of method
    ):
        if "cond" in subtask.keys():  # == inline
            return self._parse_inline(subtask, method_params)

        task_name = subtask["name"]

        task: Union[htn.Task, model.Action]
        if self.problem.has_task(task_name):
            task = self.problem.get_task(task_name)
        elif self.problem.has_action(task_name):
            task = self.problem.action(task_name)
        else:
            return None
        assert isinstance(task, htn.Task) or isinstance(task, model.Action)

        # 1: Find subtask params
        subt_params = self._parse_params(subtask["params"])

        # task_params = OrderedDict({p.name: p.type for p in task.parameters})
        # Set the variable names of task_params to subt_params
        params = OrderedDict()

        for s_p in subt_params.items():
            type_method_param = method_params.get(s_p[0], s_p[1])
            if is_compatible_type(s_p[1], type_method_param):
                params[s_p[0]] = type_method_param
            else:
                params[s_p[0]] = s_p[1]

        # for s_p, t_p in zip(subt_params.items(), task_params.items()):
        #     params[s_p[0]] = t_p[1]

        # 3: Parse exp adding ? to each variable (somehow without ? it fails)
        parameters = [
            self._parse_exp({}, "?" + str(param), {}, params) for param in subt_params
        ]

        # Create and return Subtask
        return htn.Subtask(task, *parameters)

    # _________________________________________________________

    # NOTE: Derived are declared in :predicates, so no need to add
    # fluent to problem here, they are already in there
    def _parse_derived(self, derived: OrderedDict) -> Dict[str, List[FNode]]:
        name = derived[1][0]
        params = self._parse_params(derived[1][1])

        fluents = []
        for exp in derived.get("exp", None):  # Add fluents
            fluents.append(exp)

        # Get params as list(str)
        header = params.keys()
        header = ["?" + x for x in header]  # Append '?' at the beginning

        self.derived[name] = (header, fluents, params)

        # TODO: Wait for discussion #247 before deleting this
        # return model.Derived(name, self._tm.BoolType(), params, self._env, fluents)

    # Replace arguments of exp in d
    def _change_derived(self, exp, derived, params):
        """Changes exp (the content defined inside a derived) with the
        corresponding variables used in the context (params)
        """
        if isinstance(exp, str):
            if exp in derived:
                index = derived.index(exp)
                return params[index]
            else:
                return exp

        # exp is a list, loop into it
        for i in range(len(exp)):
            # Replace each argument
            exp[i] = self._change_derived(exp[i], derived, params)

        return exp


    def _get_delay_from_date(self, date) -> Fraction:
        if not self.time_customization:
            raise UPUsageError(":customization tag required for dates")

        time = datetime.strptime(date, self.time_customization["format"])

        delta = time - self.time_customization["start"]

        delay = delta.total_seconds()

        if "minutes" in self.time_customization["unit"]:
            delay /= 60
        elif "hours" in self.time_customization["unit"]:
            delay /= 3600
        elif "days" in self.time_customization["unit"]:
            delay /= (3600 * 24) # We could use delta.days but it rounds to nearer day

        return Fraction(delay)


    def parse_problem(
        self, domain_filename: str, problem_filename: typing.Optional[str] = None
    ) -> "model.Problem":
        """
        Takes in input a filename containing the `HPDL` domain and optionally a filename
        containing the `HPDL` problem and returns the parsed `Problem`.

        Note that if the `problem_filename` is `None`, an incomplete `Problem` will be returned.

        :param domain_filename: The path to the file containing the `HPDL` domain.
        :param problem_filename: Optionally the path to the file containing the `HPDL` problem.
        :return: The `Problem` parsed from the given HPDL domain + problem.
        :return: The `Problem` parsed from the given pddl domain + problem.
        """
        domain_res = self._pp_domain.parseFile(domain_filename)

        self.problem = self._build_problem(domain_res["name"], domain_res["features"])
        self.types_map: Dict[str, "model.Type"] = {}
        self.object_type_needed: bool = self._check_if_object_type_is_needed(domain_res)
        self.universal_assignments: Dict["model.Action", List[ParseResults]] = {}
        self.has_actions_cost = False
        ##

        for types_list in domain_res.get("types", []):
            self._parse_types(types_list)

        # Check object type is defined
        if (
            self.object_type_needed and "object" not in self.types_map
        ):  # The object type is needed, but has not been defined
            self.types_map["object"] = self._env.type_manager.UserType(
                "object", None
            )  # We manually define it.

        # For HPDL number is an implicit type. We manually define it.
        if "number" not in self.types_map:
            self.types_map["number"] = self._env.type_manager.IntType(None, None)

        for p in domain_res.get("predicates", []):
            fluent = self._parse_predicate(p)
            self.problem.add_fluent(fluent)

        for f in domain_res.get("functions", []):
            func = self._parse_function(f)
            self.problem.add_fluent(func)

        # Must go after functions, as they can be used in derived
        for d in domain_res.get("derived", []):
            self._parse_derived(d)

        for c in domain_res.get("constants", []):
            objects = self._parse_constant(c)
            for o in objects:
                self.problem.add_object(o)

        for task in domain_res.get("tasks", []):
            assert isinstance(self.problem, htn.HierarchicalProblem)
            task_model = self._build_task(task)
            self.problem.add_task(task_model)

        for a in domain_res.get("actions", []):
            if not "duration" in a:
                parsed_action = self._parse_action(a)
                action_model = self._build_action(
                    parsed_action["name"],
                    parsed_action["params"],
                    parsed_action["pre"],
                    parsed_action["eff"],
                    parsed_action["durative"],
                    parsed_action["duration"],
                )
                self.problem.add_action(action_model)
            else:
                action_model = self._build_durative_action(
                    a["name"],
                    self._parse_params(a["params"]),
                    a["duration"][0],
                    a["pre"][0],
                    a["eff"][0],
                )
                self.problem.add_action(action_model)

        # Methods are defined inside tasks;
        # we need to parse them after all tasks and actions have been defined
        # because _parse_subtasks() needs to be able to find them.
        for task in domain_res.get("tasks", []):
            for method in task.get("methods", []):
                method_model = self._build_method(method, task["name"])
                self.problem.add_method(method_model)

        # Parse problem
        if problem_filename is not None:
            problem_res = self._pp_problem.parseFile(problem_filename)

            self.problem.name = problem_res["name"]

            # Get time customization
            customization = problem_res.get("customization", None)
            self.time_customization = {}
            if customization is not None:

                self.time_customization["format"] = customization[1][2][1:-1] # remove string marks
                self.time_customization["horizon-relative"] = int(customization[2][2])
                self.time_customization["start"] = datetime.strptime(customization[3][2][1:-1], self.time_customization["format"])
                self.time_customization["unit"] = customization[4][2]

            objects = problem_res.get("objects", [])
            objects = self._parse_params(objects)

            for var, kind in objects.items():
                self.problem.add_object(model.Object(var, kind, self.problem.env))

            # TODO: Why should we do this?
            # We lose problem objects as it does some kind of grounding, and SIADEX
            # does not need that
            # Add universal_assignments (forall, something-else?)
            for action, eff_list in self.universal_assignments.items():
                for eff in eff_list:
                    # Parse the variable definition part and create 2 lists, the first one with the variable names,
                    # the second one with the variable types.
                    vars_string = " ".join(eff[1])
                    vars_res = self._pp_parameters.parseString(vars_string)
                    var_names: List[str] = []
                    var_types: List["model.Type"] = []

                    for g in vars_res["params"]:
                        t = self.types_map[g[1] if len(g) > 1 else "object"]
                        for o in g[0]:
                            var_names.append(f"?{o}")
                            var_types.append(t)

                    # for each variable type, get all the objects of that type and calculate the cartesian
                    # product between all the given objects and iterate over them, changing the variable assignments
                    # in the added effect
                    for objects in product(
                        *(self.problem.objects(t) for t in var_types)
                    ):
                        assert len(var_names) == len(objects)
                        assignments = {
                            name: obj for name, obj in zip(var_names, objects)
                        }
                        if isinstance(action, model.InstantaneousAction):
                            self._add_effect(
                                action,
                                self.types_map,
                                None,
                                eff[2],
                                assignments=assignments,
                            )
                        elif isinstance(action, model.DurativeAction):
                            # TODO: There should be another way for this
                            # Create dict with params and its types
                            params = OrderedDict(
                                [(k, v.type) for k, v in action._parameters.items()]
                            )

                            self._add_timed_effects(
                                action,
                                params,
                                None,
                                eff[2],
                                assignments=assignments,
                            )
                        else:
                            raise NotImplementedError

            for i in problem_res.get("init", []):
                if i[0] == "=":
                    self.problem.set_initial_value(
                        self._parse_exp({}, i[1]),
                        self._parse_exp({}, i[2]),
                    )
                elif (  # "and" TI
                    len(i) == 3 and i[0] == "at" and
                        (i[1].replace(".", "", 1).isdigit() or 
                         i[1][0] == '"' or i[1][0] == "'") # date
                ):
                    if i[1].replace(".", "", 1).isdigit(): # digit
                        delay = Fraction(i[1])
                    else: # date
                        # Removing string marks
                        delay = self._get_delay_from_date(i[1][1:-1])

                    ti = model.StartTiming(delay)

                    va = self._parse_exp({}, i[2])
                    if va.is_fluent_exp():
                        self.problem.add_timed_effect(ti, va, self._em.TRUE())
                    elif va.is_not():
                        self.problem.add_timed_effect(ti, va.arg(0), self._em.FALSE())
                    elif va.is_equals():
                        self.problem.add_timed_effect(ti, va.arg(0), va.arg(1))
                    else:
                        raise SyntaxError(f"Not able to handle this TIL {i}")
                elif (  # "between" TI
                    len(i) == 4
                    and i[0] == "between"
                    and (i[1].replace(".", "", 1).isdigit() or 
                         i[1][0] == '"' or i[1][0] == "'")
                    and (i[2].replace(".", "", 1).isdigit() or 
                         i[2][0] == '"' or i[2][0] == "'")
                ):
                    # TODO: I believe it could be included as an interval, but
                    # for the moment I'll keep it as two time effects, the
                    # provided and the not
                    
                    # Get delays
                    if i[1].replace(".", "", 1).isdigit(): # digit
                        lower_delay = Fraction(i[1])
                    else: # date
                        # Removing string marks
                        lower_delay = self._get_delay_from_date(i[1][1:-1])

                    if i[2].replace(".", "", 1).isdigit(): # digit
                        upper_delay = Fraction(i[2])
                    else: # date
                        # Removing string marks
                        upper_delay = self._get_delay_from_date(i[2][1:-1])

                    lower = model.StartTiming(lower_delay)
                    upper = model.StartTiming(upper_delay)

                    # ti = model.ClosedTimeInterval(lower, upper)
                    va = self._parse_exp({}, i[3])
                    if va.is_fluent_exp():
                        self.problem.add_timed_effect(lower, va, self._em.TRUE())
                        self.problem.add_timed_effect(upper, va, self._em.FALSE())
                    elif va.is_not():
                        self.problem.add_timed_effect(
                            lower, va.arg(0), self._em.FALSE()
                        )
                        self.problem.add_timed_effect(upper, va.arg(0), self._em.TRUE())
                    elif va.is_equals():
                        # self.problem.add_timed_effect(ti, va.arg(0), va.arg(1))
                        raise NotImplementedError(
                            f"No support between assignments TIL {i}"
                        )
                    else:
                        raise SyntaxError(f"Not able to handle this TIL {i}")
                else:
                    self.problem.set_initial_value(
                        self._parse_exp({}, i),
                        self._em.TRUE(),
                    )

            # HPDL task-goal is the equivalent of HDDL htn tasks
            # TODO: Add ordering to task_network
            tasknet = problem_res.get("goal", None)
            if tasknet is not None:
                subtasks, _ = self._parse_method(tasknet, self.types_map)

                # Add subtasks to task_network
                for ordering in subtasks:
                    for s in ordering[1]:
                        self.problem.task_network.add_subtask(s)

                    if ordering[0] == "(":
                        self.problem.task_network.set_ordered(*ordering[1])

                # All subtasks from the next iteration have sequential order with
                # respect to the previous iteration
                # TODO: Refactor, this looks ugly and is not easy to understand
                if len(ordering) >= 2:
                    for i in range(1, len(subtasks)):  # ordering[0]...ordering[n]
                        # Loop through subtasks of previous ordering
                        if subtasks[i - 1][0] == "(" and subtasks[i][0] == "(":
                            # Both sequential, last vs first
                            s1 = subtasks[i - 1][1][
                                -1
                            ]  # Next ordering, subtask list, last task
                            s2 = subtasks[i][1][0]
                            self.problem.task_network.set_ordered(s1, s2)

                        elif subtasks[i - 1][0] == "[" and subtasks[i][0] == "(":
                            # Parallel-sequential, all vs first
                            s2 = subtasks[i][1][0]
                            for s1 in subtasks[i - 1][1]:
                                self.problem.task_network.set_ordered(s1, s2)

                        elif subtasks[i - 1][0] == "(" and subtasks[i][0] == "[":
                            # Sequential-parallel, last vs all
                            s1 = subtasks[i - 1][1][-1]
                            for s2 in subtasks[i][1]:
                                self.problem.task_network.set_ordered(s1, s2)

                        else:  # Both parallel, all vs all
                            for s1 in subtasks[i - 1][1]:
                                for s2 in subtasks[i][1]:
                                    self.problem.task_network.set_ordered(s1, s2)

            self.has_actions_cost = (
                self.has_actions_cost and self._problem_has_actions_cost(self.problem)
            )

            optimization = problem_res.get("optimization", None)
            metric = problem_res.get("metric", None)

            if metric is not None:
                if (
                    optimization == "minimize"
                    and len(metric) == 1
                    and metric[0] == "total-time"
                ):
                    self.problem.add_quality_metric(model.metrics.MinimizeMakespan())
                else:
                    metric_exp = self._parse_exp({}, metric)
                    if (
                        self.has_actions_cost
                        and optimization == "minimize"
                        and metric_exp == self._totalcost
                    ):
                        costs = {}
                        self.problem._fluents.remove(self._totalcost.fluent())
                        self.problem._initial_value.pop(self._totalcost)
                        use_plan_length = all(
                            False for _ in self.problem.durative_actions
                        )
                        for a in self.problem.instantaneous_actions:
                            cost = None
                            for e in a.effects:
                                if e.fluent == self._totalcost:
                                    cost = e
                                    break
                            if cost is not None:
                                costs[a] = cost.value
                                a._effects.remove(cost)
                                if cost.value != 1:
                                    use_plan_length = False
                            else:
                                use_plan_length = False
                        if use_plan_length:
                            self.problem.add_quality_metric(
                                model.metrics.MinimizeSequentialPlanLength()
                            )
                        else:
                            self.problem.add_quality_metric(
                                model.metrics.MinimizeActionCosts(
                                    costs, self._em.Int(0)
                                )
                            )
                    else:
                        if optimization == "minimize":
                            self.problem.add_quality_metric(
                                model.metrics.MinimizeExpressionOnFinalState(metric_exp)
                            )
                        elif optimization == "maximize":
                            self.problem.add_quality_metric(
                                model.metrics.MaximizeExpressionOnFinalState(metric_exp)
                            )
        else:
            if len(self.universal_assignments) != 0:
                raise UPUsageError(
                    "The domain has quantified assignments. In the unified_planning library this is compatible only if the problem is given and not only the domain."
                )
        return self.problem
