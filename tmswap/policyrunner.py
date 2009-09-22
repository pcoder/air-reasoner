#!/usr/bin/env python
"""Amord.py

an attempt at an amord implementation built on cwm

"""

import weakref
#WVD = weakref.WeakValueDictionary
WVD = dict
from collections import deque
from itertools import chain

import llyn
from formula import Formula, StoredStatement
from term import List, Env, Symbol, Term, BuiltIn

import uripath

import diag
progress = diag.progress

import tms
import rete
import treat

MM = rete # or it could be treat

OFFLINE = [False]

from prooftrace import (supportTrace,
                        removeFormulae,
                        removeBaseRules,
                        simpleTraceOutput,
                        rdfTraceOutput)

from py25 import all, any, defaultdict
                        

workcount = defaultdict(int)

GOAL = 1

debugLevel = 0

# Need to extend the List and Tuple types to do x.substitution()
def baseSub(value, bindings):
    if value is not None:
        return value.substitution(bindings)
    else:
        return None

class SubstitutingList(list):
    pass

SubstitutingList.substitution = \
    lambda self, bindings: \
        SubstitutingList(map(lambda x: baseSub(x, bindings), self))

class SubstitutingTuple(tuple):
    pass

SubstitutingTuple.substitution = \
    lambda self, bindings: \
        SubstitutingTuple(map(lambda x: baseSub(x, bindings), self))

class FormulaTMS(object):
    """This is the interface between the TMS and the rdf side of things
It keeps a Formula of all facts currently believed.
The job of activating rules also goes on this
"""
    tracking = True
    def __init__(self, workingContext):
        self.tms = tms.TMS('FormulaTMS', self.event)
        self.nodes = WVD()
        self.workingContext = workingContext
        workingContext.tms = self
        self.NS = workingContext.newSymbol('http://dig.csail.mit.edu/2007/cwmrete/tmswap/amord')
        self.formulaContents = self.NS['FormulaContents']
        self.parseSemantics = workingContext.store.semantics
        self.premises = set()
        self.falseAssumptions = set()
        self.contexts = {}

        self.assumedPolicies = []
        self.assumedURIs = []
        self.assumedStrings = []
        self.assumedClosedWorlds = []
        

    def getAuxTriple(self, auxid, subject, predicate, object, variables):
        """An aux triple is a triple supported for something other than belief
This is currently only used for goals
"""
        if (auxid, subject, predicate, object, variables) not in self.nodes:
            a = tms.Node(self.tms, (auxid, subject, predicate, object, variables))
            self.nodes[(auxid, subject, predicate, object, variables)] = a
        return self.nodes[(auxid, subject, predicate, object, variables)]

    def justifyAuxTriple(self, auxid, subject, predicate, object, variables, rule, antecedents):
        auxnode = self.getAuxTriple(auxid, subject, predicate, object, variables)
        node = self.getTriple(subject, predicate, object)
        a = tms.AndExpression(list(antecedents))
        n = tms.NotExpression(node)
        auxnode.justify(rule, [a,n])


    def getContext(self, id):
        if id not in self.contexts:
            self.contexts[id] = self.workingContext.newFormula()
        return self.contexts[id]

    def getTriple(self, subject, predicate, object, variables=None):
        if (subject, predicate, object, variables) not in self.nodes:
            a = tms.Node(self.tms, (subject, predicate, object, variables))
            self.nodes[(subject, predicate, object, variables)] = a
        return self.nodes[(subject, predicate, object, variables)]

    def getThing(self, thing):
        if thing not in self.nodes:
            a = tms.Node(self.tms, thing)
            self.nodes[thing] = a
        return self.nodes[thing]

    def getStatement(self, (subject, predicate, object, variables)):
        return self.workingContext.statementsMatching(subj=subject, pred=predicate, obj=object)[0]

    def getAuxStatement(self, (auxid, subject, predicate, object, variables)):
        return self.getContext(auxid).statementsMatching(subj=subject, pred=predicate, obj=object)[0]

    def event(self, node, justification):
        if isinstance(justification, tms.Premise):
            if isinstance(node.datum, tuple) and len(node.datum) == 2:
                pass # Better than the alternative?
            else:
                self.premises.add(node)
        if justification is False:
            if isinstance(node.datum, Rule):
                pass # not worth the work
            if isinstance(node.datum, tuple):
                if len(node.datum) == 4:
                    self.workingContext.removeStatement(self.getStatement(node.datum))
                    self.getContext(GOAL).removeStatement(self.getStatement(node.datum))
                else:
                    self.getContext(GOAL).removeStatement(self.getAuxStatement(node.datum))
        if isinstance(node.datum, Rule):
            if debugLevel >= 3:
                if node.datum.goal:
                    progress('\tNow supporting goal rule %s because of %s' % (node, justification))
                else:
                    progress('\tNow supporting rule %s because of %s' % (node, justification))
            if self.tracking:
                if node.datum.goal:
                    workcount['goal-rule'] += 1
                else:
                    workcount['rule'] += 1
            node.datum.compileToRete()
            if debugLevel >= 4:
                progress('\t\t ... built rule')
        if isinstance(node.datum, Symbol):
            if debugLevel >= 2:
                progress('Now supporting %s because of %s' % (node, justification))
            f = _loadF(self, node.datum.uriref())
            self.getThing(f).justify(self.parseSemantics, [node])
        if isinstance(node.datum, Formula):
            if debugLevel >= 2:
                progress('Now supporting %s because of %s' % (node, justification))
            self.workingContext.loadFormulaWithSubstitution(node.datum)
        if isinstance(node.datum, tuple):
#            print '\t ... now supporting %s because of %s' % (node, justification)
            if len(node.datum) == 2:
                pass # signal data
            elif len(node.datum) == 4:
                if isinstance(node.datum[1], BuiltIn) and node.datum[1] is not node.datum[1].store.sameAs:
                     # Hackety hack...  Dun like it, but it avoids
                     # inserting a (wrong) built-in fact...
                     return
                if self.tracking:
                    workcount['fact'] += 1
                triple = node.datum[:3]
                variables = node.datum[3]
                if variables is None:
                    self.workingContext.add(*triple)                
                    self.getContext(GOAL).add(*triple)
                else:  # slow path --- do we need it?
                    s, p, o = triple
                    s1 = self.workingContext._buildStoredStatement(subj=s,
                                                                 pred=p,
                                                                 obj=o,
                                                                why=None)
                    if isinstance(s1, int): # It is possible for cwm to not directly add things
                        raise TypeError(node)
                    s1.variables = v
                    result = self.workingContext. _addStatement(s1)
                    
                    s2 = getContext(GOAL)._buildStoredStatement(subj=s,
                                                                 pred=p,
                                                                 obj=o,
                                                                why=None)
                    if isinstance(s2, int):
                        raise TypeError(node)
                    s2.variables = v
                    result = self.getContext(GOAL). _addStatement(s1)
            else:
                if self.tracking:
                    workcount['goal'] += 1
                if debugLevel > 7:
                    progress('\t ... now supporting goal %s because of %s' % (node, justification))
                c, s, p, o, v = node.datum
                statement = self.getContext(c)._buildStoredStatement(subj=s,
                                                                 pred=p,
                                                                 obj=o,
                                                                why=None)
                if isinstance(statement, int):
                    raise TypeError(node)
                statement.variables = v
                result = self.getContext(c). _addStatement(statement)
#                print 'added %s, a total of %s statements' % (statement, result)
                
        

def canonicalizeVariables(statement, variables):
    """Canonicalize all variables in statement to be URIs of the form
    http://example.com/vars/#variable(digits).  Returns a tuple
    corresponding to the subject, predicate, and object following
    canonicalization, and a frozenset including the URIs of all
    variables that were canonicalized.
    
    """
    subj, pred, obj = statement.spo()
    store = statement.context().store
    varMapping = {}
    count = [0]
    def newVariable():
        count[0] += 1
        return store.newSymbol('http://example.com/vars/#variable%s' % count[0])
    def canNode(node):
        if node in varMapping:
            return varMapping[node]
        if node in variables:
            varMapping[node] = newVariable()
            return varMapping[node]
        if isinstance(node, List):
            return node.store.newList([canNode(x) for x in node])
        if isinstance(node, Formula):
            # Commenting this out for log:includes.  What side-effects
            # does this have?
#            if node.occurringIn(variables):
#                raise ValueError(node)
#            return node
            
            # log:includes uses external scope to canonicalize
            # variables...?
            f = None
            for statement in node.statements:
                subj, pred, obj = statement.spo()
                if subj is not canNode(subj) or pred is not canNode(pred) or obj is not canNode(obj):
                    f = node.store.newFormula()
            if f:
                for statement in node.statements:
                    subj, pred, obj = statement.spo()
                    f.add(canNode(subj), canNode(pred), canNode(obj))
                f.close()
                return f
        return node
    return (canNode(subj), canNode(pred), canNode(obj)), frozenset(varMapping.values())

class Assertion(object):
    """An assertion can be asserted. It tracks what its support will be when asserted
"""
    def __init__(self, pattern, support=None, rule=None, validToRename=None):
        self.pattern = pattern
        self.support = support
        self.rule = rule
        
        if isinstance(pattern, Formula):
            if validToRename is None:
                self.needsRenaming = frozenset(pattern.existentials())
            else:
                self.needsRenaming = frozenset(pattern.existentials()).intersection(validToRename)
        else:
            self.needsRenaming = frozenset()

    def substitution(self, bindings):
        if self.support is None:
            support = None
        else:
            supportList = []
            for x in self.support:
                if isinstance(x, frozenset):
                    supportList.append(x)
                else:
                    supportList.append(x.substitution(bindings))
            support = frozenset(supportList)

        newBlankNodesBindings = dict([(x, self.pattern.newBlankNode()) for x in self.needsRenaming]) # if invalid, will not be run
        bindings.update(newBlankNodesBindings)

        return Assertion(self.pattern.substitution(bindings), support, self.rule, validToRename=newBlankNodesBindings.values())

    def __repr__(self):
        return 'Assertion(%s,%s,%s)' % (self.pattern, self.support, self.rule)

    def __eq__(self, other):
        return isinstance(other, Assertion) and self.pattern == other.pattern

    def __hash__(self):
        return hash((self.pattern, self.__class__)) 

    
        

class AuxTripleJustifier(object):
    """A thunk, to separate the work of creating aux triples from
building the rules whose support created them.
These are then passed to the scheduler
"""
    def __init__(self, tms, *args):
        self.tms = tms
        self.args = args

    def __call__(self):
        self.tms.justifyAuxTriple(*self.args)

class RuleName(object):
    def __init__(self, name, descriptions):
        assert isinstance(name, Term)
        assert all(isinstance(x, Term) for x in descriptions)
        self.name = name
        self.descriptions = descriptions

    def __repr__(self):
        return 'R(%s)' % (self.name,)

    def uriref(self): # silly
        return self.name.uriref() + '+'.join(''.join(str(y) for y in x) for x in self.descriptions)


class RuleFire(object):
    """A thunk, passed to the scheduler when a rule fires
"""
    def __init__(self, rule, triples, env, penalty, result, alt=False):
        self.rule = rule
        self.args = (triples, env, penalty, result, alt)

    def __call__(self):
        triples, env, penalty, result, alt = self.args
        self = self.rule
        if alt and self.success: # We fired after all
#            raise RuntimeError('we do not have any alts yet')
            return
        if debugLevel > 12:
            if alt:
                progress('%s failed, and alt is being called' % (self.label,))
            else:
                progress('%s succeeded, with triples %s and env %s' % (self.label, triples, env))
        triplesTMS = []
        goals = []
        unSupported = []
        for triple in triples:
            t = self.tms.getTriple(*triple.spo())
            if t.supported:
                triplesTMS.append(t)
            else:
                t2 = self.tms.getAuxTriple(GOAL, triple.subject(), triple.predicate(), triple.object(), triple.variables)
                if t2.supported:
                    goals.append((triple, t2))
                else:
                    unSupported.append(triple)

        if self.matchName:
            if self.matchName in env:
                return
            env = env.bind(self.matchName, (frozenset(triplesTMS + [x[1] for x in goals]), Env()))
        if goals and unSupported:
            raise RuntimeError(goals, unSupported) #This will never do!
        elif goals:
            if not self.goal:
                raise RuntimeError('how did I get here?\nI matched %s, which are goals, but I don\'t want goals' % goals)
#                print 'we goal succeeded! %s, %s' % (triples, result)
            envDict = env.asDict()
            for triple, _ in goals:
                assert not triple.variables.intersection(env.keys())
                newVars = triple.variables.intersection(envDict.values())
                if newVars:
                    raise NotImplementedError("I don't know how to add variables")
            
            for r in result:
                # Do any substitution and then extract the description
                # and r12 from the particular r's tuple.
                r12 = r.substitution(env.asDict())
                desc = r12[1]
                r12 = r12[0]
                
                r2 = r12.pattern
                support = r12.support
                ruleId = r12.rule
                assert isinstance(r2, Rule) or not r2.occurringIn(self.vars), (r2, env, penalty, self.label)
#            print '   ...... so about to assert %s' % r2
                r2TMS = self.tms.getThing(r2)
                if support is None:
                    r2TMS.justify(self.sourceNode, triplesTMS + [self.tms.getThing(self)])
                else:
                    supportTMS = reduce(frozenset.union, support, frozenset())
                    r2TMS.justify(ruleId, supportTMS)
                assert self.tms.getThing(self).supported
                assert r2TMS.supported                
#                raise NotImplementedError(goals) #todo: handle goals
        elif unSupported:
            raise RuntimeError(triple, self) # We should never get unsupported triples
        else:
            if self.goal:
                return
#                print 'we succeeded! %s, %s' % (triples, result)
            if alt:
#                closedWorld = self.tms.getThing(('closedWorld', self.tms.workingContext.newList(list(self.tms.premises))))
                closedWorld = self.tms.getThing(('closedWorld',
                                                 self.tms.workingContext.newList(self.tms.assumedPolicies +
                                                     self.tms.assumedURIs +
                                                     self.tms.assumedStrings +
                                                     self.tms.assumedClosedWorlds)))
                closedWorld.assume()
                self.tms.assumedClosedWorlds.append(closedWorld)
                altSupport = [closedWorld]
#                desc = self.altDescriptions
            else:
                altSupport = []
#                desc = [x.substitution(env.asDict()) for x in self.descriptions]

            for r in result:
                # Do any substitution and then extract the description
                # and r12 from the particular r's tuple.
                r12 = r.substitution(env.asDict())
                desc = r12[1]
                r12 = r12[0]
                
                r2 = r12.pattern
                support = r12.support
                ruleId = r12.rule
                assert isinstance(r2, Rule) or not r2.occurringIn(self.vars), (r2, env, penalty, self.label)
#            print '   ...... so about to assert %s' % r2
                r2TMS = self.tms.getThing(r2)
                if support is None:
                    r2TMS.justify(RuleName(self.sourceNode, desc), triplesTMS + [self.tms.getThing(self)] + altSupport)
                else:
                    supportTMS = reduce(frozenset.union, support, frozenset()).union(altSupport)
                    r2TMS.justify(RuleName(ruleId, desc), supportTMS)
                assert self.tms.getThing(self).supported
                assert r2TMS.supported


class Rule(object):
    """A Rule contains all of the information necessary to build the rete
for a rule, and to handle when the rule fires. It does not care
much how the rule was represented in the rdf network
"""

    baseRules = set()
    
    def __init__(self, eventLoop, tms, vars, label,
                 pattern, contextFormula, result, alt, sourceNode,
                 goal=False, matchName=None, base=False, ellipsis=False,
                 generated=False):
        self.generatedLabel = False
        if label is None or label=='None':
            self.generatedLabel = True
            if not goal:
                label = '[pattern=%s]' % pattern
            else:
                label= '[goal=%s]' % pattern
        self.label = label
        self.eventLoop = eventLoop
        self.success = False
        self.tms = tms
        self.vars = vars | pattern.existentials()
        self.pattern = pattern
        self.patternToCompare = frozenset([x.spo() for x in pattern])
        self.contextFormula = contextFormula
        self.result = result
#        self.descriptions = descriptions
        self.alt = alt
#        assert isinstance(altDescriptions, list), altDescriptions
#        self.altDescriptions = altDescriptions
        self.goal = goal
        self.matchName = matchName
        self.sourceNode = sourceNode
        self.generated = generated
        self.isBase = base
        self.isEllipsis = ellipsis
        if base or ellipsis:
            self.baseRules.add(sourceNode)
        if debugLevel > 15:        
            print '''just made a rule, with
        tms=%s,
        vars=%s
        label=%s
        pattern=%s
        result=%s
        alt=%s
        matchName=%s''' % (tms, self.vars, label, pattern, result, alt, matchName)


    def __eq__(self, other):
        return isinstance(other, Rule) and \
               self.eventLoop is other.eventLoop and \
               self.tms is other.tms and \
               self.goal == other.goal and \
               self.vars == other.vars and \
               self.patternToCompare == other.patternToCompare and \
               self.result == other.result and \
               self.alt == other.alt and \
               self.matchName == other.matchName

    def __hash__(self):
        assert not isinstance(Rule, list)
        assert not isinstance(self.eventLoop, list)
        assert not isinstance(self.tms, list)
        assert not isinstance(self.vars, list)
        assert not isinstance(self.pattern, list)
        assert not isinstance(self.sourceNode, list)
        assert not isinstance(self.goal, list)
        assert not isinstance(self.matchName, list)
        return hash((Rule, self.eventLoop, self.tms, self.vars, self.pattern, self.sourceNode, self.goal, self.matchName))

    def __repr__(self):
        return '%s with vars %s' % (self.label.encode('utf_8'), self.vars)

    def compileToRete(self):
        patterns = self.pattern.statements
        if self.goal:
            workingContext = self.tms.getContext(GOAL)
        else:
            workingContext = self.tms.workingContext
            for triple in patterns:
                    (s, p, o), newVars = canonicalizeVariables(triple, self.vars)
                    self.eventLoop.add(AuxTripleJustifier(self.tms, GOAL, s, p, o, newVars, self.sourceNode, [self.tms.getThing(self)]))
        index = workingContext._index
        bottomBeta = MM.compilePattern(index, patterns, self.vars, self.contextFormula, buildGoals=False, goalPatterns=self.goal, supportBuiltin=self.addTriple)
        trueBottom =  MM.ProductionNode(bottomBeta, self.onSuccess, self.onFailure)
        return trueBottom

    def addTriple(self, triple):
        self.tms.getTriple(*triple.spo()).assume()
    def retractTriple(self, triple):
        self.tms.getTriple(*triple.spo()).retract()

    def onSuccess(self, (triples, environment, penalty)):
        self.success = True
        self.eventLoop.add(RuleFire(self, triples, environment, penalty, self.result))

    def onFailure(self):
        assert not self.success
        if self.alt:
            self.eventLoop.addAlternate(RuleFire(self, [], Env(), 0, self.alt, alt=True))

    def substitution(self, env):
        if not env:
            return self
        pattern = self.pattern.substitution(env)
        result = [x.substitution(env) for x in self.result]
        alt = [x.substitution(env) for x in self.alt]
#        descriptions = [x.substitution(env) for x in self.descriptions]
#        altDescriptions = [x.substitution(env) for x in self.altDescriptions]
        if self.generatedLabel:
            label = None
        else:
            label = self.label
        return self.__class__(self.eventLoop, self.tms, self.vars,
                              label, pattern, self.contextFormula, result, alt,
                              self.sourceNode, self.goal, self.matchName, base=self.isBase, ellipsis=self.isEllipsis, generated=True)

    @classmethod
    def compileFromTriples(cls, eventLoop, tms, F, ruleNode, goal=False, vars=frozenset(), preboundVars=frozenset(), base=False):
        assert tms is not None
        rdfs = F.newSymbol('http://www.w3.org/2000/01/rdf-schema')
        rdf = F.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
        p = F.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')

#        vars = vars.union(F.each(subj=node, pred=p['variable']))
        # Find the variables in this rule.
        vars = vars.union(F.universals())
        varBinding = len(vars - preboundVars) > 0
        preboundVars = preboundVars.union(F.universals())

        realNode = ruleNode
        nodes = [realNode]

        # Get the air:then and air:else nodes.
        thenNodes = F.each(subj=ruleNode, pred=p['then'])
        elseNodes = F.each(subj=ruleNode, pred=p['else'])
        if varBinding and len(elseNodes) > 0:
            raise ValueError('%s has an air:else clause even though a variable is bound in its air:if, which is unsupported (did you mean to use @forSome instead of @forAll?)'
                             % (ruleNode))
#        if altNode:
#            nodes.append(altNode)
#            altDescriptions = list(F.each(subj=altNode, pred=p['description']))
#        else:
#            altDescriptions = []

        # Get the air:label and air:if values.
        # TODO: get this annotated on the ontology.
        label = F.the(subj=ruleNode, pred=p['label'])
        try:
            pattern = F.the(subj=ruleNode, pred=p['if'])
        except AssertionError:
            raise ValueError('%s has too many air:if clauses, being all of %s'
                             % (ruleNode, F.each(subj=ruleNode, pred=p['if'])))
        if pattern is None:
            raise ValueError('%s must have an air:if clause. You did not give it one' % (ruleNode,))
        
        # Is the rule an air:Hidden-rule?
        base = base or (F.contains(subj=ruleNode, pred=F.store.type, obj=p['Hidden-rule']) == 1)
        # air:Ellipsed-rule?
        ellipsis = (F.contains(subj=ruleNode, pred=F.store.type, obj=p['Ellipsed-rule']) == 1)
#        descriptions = list(F.each(subj=node, pred=p['description']))

        # Collect all air:then or air:else actions...
        resultList  = []
        
        # For each air:then node...
        subrules = []
        goal_subrules = []
        assertions = []
        goal_assertions = []
        for node in thenNodes:
            actions = []
            
            # Get any description...
            try:
                description = F.the(subj=node, pred=p['description'])
                if description == None:
                    description = SubstitutingList()
                else:
                    description = SubstitutingList([description])
            except AssertionError:
                raise ValueError('%s has too many descriptions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['description'])))
            
            # Get any subrule...
            subrule = None
            try:
                subruleNode = F.the(subj=node, pred=p['rule'])
                if subruleNode is not None:
                    subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, subruleNode, vars=vars, preboundVars=preboundVars, base=base))
            except AssertionError:
                raise ValueError('%s has too many rules in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['rule'])))
            if subrule is not None:
                subrules.append(SubstitutingTuple((subrule, description)))
                actions.append(subrule)
            
            # Get any goal-subrule...
            goal_subrule = None
            try:
                goal_subruleNode = F.the(subj=node, pred=p['goal-rule'])
                if goal_subruleNode is not None:
                    goal_subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, goal_subruleNode, goal=True, vars=vars, preboundVars=preboundVars, base=base))
            except AssertionError:
                raise ValueError('%s has too many goal-rules in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['goal-rule'])))
            if goal_subrule is not None:
                goal_subrules.append(
                    SubstitutingTuple((goal_subrule, description)))
                actions.append(goal_subrule)
            
            # Get any assertion...
            try:
                assertion = F.the(subj=node, pred=p['assert'])
            except AssertionError:
                raise ValueError('%s has too many assertions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert'])))
            if assertion is not None:
                assertions.append(SubstitutingTuple((assertion, description)))
                actions.append(assertion)
            
            # Get any goal-assertion...
            try:
                goal_assertion = F.the(subj=node, pred=p['assert-goal'])
            except AssertionError:
                raise ValueError('%s has too many goal-assertions in an air:then, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert-goal'])))
            if goal_assertion is not None:
                goal_assertions.append(
                    SubstitutingTuple((goal_assertion, description)))
                actions.append(goal_assertion)
            
            # Make sure there was exactly one of the above.
            if len(actions) != 1:
                raise ValueError('%s has more than one of {air:rule, air:goal-rule, air:assert, air:assert-goal} in an air:then, being all of %s'
                                 % (ruleNode, actions))
            
        # Get the data from the assertions.
        assertionObjs = []
        for assertion in assertions + goal_assertions:
            description = assertion[1]
            assertion = assertion[0]
            statement = F.the(subj=assertion, pred=p['statement'])
            justNode = F.the(subj=assertion, pred=p['justification'])
            if justNode is not None:
                antecedents = frozenset(F.each(subj=justNode, pred=p['antecedent']))
            rule_id = F.the(subj=justNode, pred=p['rule-id'])
            
            if justNode is not None and rule_id is not None:
                assertionObjs.append(SubstitutingTuple(
                        (Assertion(statement, antecedents, rule_id),
                         description)))
            else:
                assertionObjs.append(SubstitutingTuple(
                        (Assertion(statement),
                         description)))
        resultList.append(subrules + assertionObjs + goal_subrules)
        
        # Now do what we did to collect the assertions and such for
        # any air:else actions.
        subrules = []
        goal_subrules = []
        assertions = []
        goal_assertions = []
        for node in elseNodes:
            actions = []
            
            # Get any description...
            try:
                description = F.the(subj=node, pred=p['description'])
                if description == None:
                    description = SubstitutingList()
                else:
                    description = SubstitutingList([description])
            except AssertionError:
                raise ValueError('%s has too many descriptions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['description'])))

            # Get any subrule...
            subrule = None
            try:
                subruleNode = F.the(subj=node, pred=p['rule'])
                if subruleNode is not None:
                    subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, subruleNode, vars=vars, preboundVars=preboundVars, base=base))
            except AssertionError:
                raise ValueError('%s has too many rules in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['rule'])))
            if subrule is not None:
                subrules.append(SubstitutingTuple((subrule, description)))
                actions.append(subrule)
            
            # Get any goal-subrule...
            goal_subrule = None
            try:
                goal_subruleNode = F.the(subj=node, pred=p['goal-rule'])
                if goal_subruleNode is not None:
                    goal_subrule = Assertion(cls.compileFromTriples(eventLoop, tms, F, goal_subruleNode, goal=True, vars=vars, preboundVars=preboundVars, base=base))
            except AssertionError:
                raise ValueError('%s has too many goal-rules in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['goal-rule'])))
            if goal_subrule is not None:
                goal_subrules.append(
                    SubstitutingTuple((goal_subrule, description)))
                actions.append(goal_subrule)
            
            # Get any assertion...
            try:
                assertion = F.the(subj=node, pred=p['assert'])
            except AssertionError:
                raise ValueError('%s has too many assertions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert'])))
            if assertion is not None:
                assertions.append(SubstitutingTuple((assertion, description)))
                actions.append(assertion)
            
            # Get any goal-assertion...
            try:
                goal_assertion = F.the(subj=node, pred=p['assert-goal'])
            except AssertionError:
                raise ValueError('%s has too many goal-assertions in an air:else, being all of %s'
                                 % (ruleNode, F.each(subj=node, pred=p['assert-goal'])))
            if goal_assertion is not None:
                goal_assertions.append(
                    SubstitutingTuple((goal_assertion, description)))
                actions.append(goal_assertion)
            
            # Make sure there was exactly one of the above.
            if len(actions) != 1:
                raise ValueError('%s has more than one of {air:rule, air:goal-rule, air:assert, air:assert-goal} in an air:else, being all of %s'
                                 % (ruleNode, actions))
            
        # Get the data from the assertions.
        assertionObjs = []
        for assertion in assertions + goal_assertions:
            description = assertion[1]
            assertion = assertion[0]
            statement = F.the(subj=assertion, pred=p['statement'])
            justNode = F.the(subj=assertion, pred=p['justification'])
            if justNode is not None:
                antecedents = frozenset(F.each(subj=justNode, pred=p['antecedent']))
            rule_id = F.the(subj=justNode, pred=p['rule-id'])###here
            
            if justNode is not None and rule_id is not None:
                assertionObjs.append(SubstitutingTuple(
                        (Assertion(statement, antecedents, rule_id),
                         description)))
            else:
                assertionObjs.append(SubstitutingTuple(
                        (Assertion(statement),
                         description)))
        resultList.append(subrules + assertionObjs + goal_subrules)
        
        node = ruleNode
        matchedGraph = F.the(subj=node, pred=p['matched-graph'])
        
        self = cls(eventLoop, tms,
                   vars, unicode(label),
                   pattern,
                   F,
                   resultList[0],
#                   descriptions=descriptions,
                   alt=resultList[1],# altDescriptions=altDescriptions,
                   goal=goal, matchName=matchedGraph, sourceNode=node, base=base, ellipsis=ellipsis)
        return self

    @classmethod
    def compileCwmRule(cls, eventLoop, tms, F, triple):
        assert tms is not None
        label = "Rule from cwm with pattern %s" % triple.subject()
        pattern = triple.subject()
        assertions = [(Assertion(triple.object()), None)]
        vars = frozenset(F.universals())
        self = cls(eventLoop, tms,
                   vars, unicode(label),
                   pattern,
                   assertions,
                   alt=[],
                   goal=False,
                   matchName=None,
                   sourceNode=pattern,
                   base=True,
                   ellipsis=False)
        return self


    @classmethod
    def compileFormula(cls, eventLoop, formulaTMS, pf, base=False):
        rdf = pf.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
        p = pf.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
        policies = pf.each(pred=rdf['type'], obj=p['Policy'])
#        globalVars = frozenset(pf.each(pred=rdf['type'], obj=p['Variable']))
        globalVars = frozenset(pf.universals())
        cwm_rules = [cls.compileCwmRule(eventLoop,
                                        formulaTMS,
                                        pf,
                                        x)
                     for x in pf.statementsMatching(pred=pf.store.implies)]
        rules = reduce(list.__add__, [[cls.compileFromTriples(eventLoop,
                                        formulaTMS,
                                        pf,
                                        x,
#                                        vars=globalVars.union(pf.each(subj=y, pred=p['variable'])),
                                        vars=globalVars.union(pf.universals()),
                                        base=base)
                        for x in pf.each(subj=y, pred=p['rule'])]
                    for y in policies], [])
        goal_rules = reduce(list.__add__, [[cls.compileFromTriples(eventLoop,
                                                       formulaTMS,
                                                       pf,
                                                       x,
#                                                       vars=globalVars.union(pf.each(subj=y, pred=p['variable'])),
                                                       vars=globalVars.union(pf.universals()),
                                                       base=base)
                        for x in pf.each(subj=y, pred=p['goal-rule'])]
                    for y in policies], [])
        return policies, rules, goal_rules, cwm_rules               





uriGenCount = [0]
def nameRules(pf, uriBase):
    rdf = pf.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
    p = pf.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
    bindings = {}
    for statement in chain(pf.statementsMatching(pred=p['rule']),
                                        pf.statementsMatching(pred=['goal-rule'])):
        node = statement.subject()
        if node in pf.existentials():
            bindings[node] = uriBase + str(uriGenCount[0])
            uriGenCount[0] += 1
    return pf.substitution(bindings)


class EventLoop(object):
    """The eventloop (there should only be one)
is a FIFO of thunks to be called.
"""
    def __init__(self):
        self.events = deque()
        self.alternateEvents = deque()

    def add(self, event):
        self.events.appendleft(event)

    def addAlternate(self, event):
        self.alternateEvents.appendleft(event)

    def next(self):
        if self.events:
            return self.events.pop()()
        return self.alternateEvents.pop()()

    def __len__(self):
        return len(self.events) + len(self.alternateEvents)


            

def setupTMS(store):
    workingContext = store.newFormula()
    workingContext.keepOpen = True
    formulaTMS = FormulaTMS(workingContext)
    return formulaTMS
    

def loadFactFormula(formulaTMS, uri, closureMode=""): #what to do about closureMode?
##    We're not ready for this yet!
##    store = formulaTMS.workingContext.store
##    s = store.newSymbol(uri)
##    assert isinstance(s, Symbol)
##    formulaTMS.getThing(s).assume()
##    return s
    f = _loadF(formulaTMS, uri, closureMode)
    formulaTMS.getThing(f).assume()
    formulaTMS.assumedURIs.append(formulaTMS.workingContext.newSymbol(uri))
    return f

def _loadF(formulaTMS, uri, closureMode=""):
    if loadFactFormula.pClosureMode:
        closureMode += "p"
    store = formulaTMS.workingContext.store
    f = store.newFormula()
    f.setClosureMode(closureMode)
    f = store.load(uri, openFormula=f)
    return f

def parseN3(store, f, string):
    import notation3
    p = notation3.SinkParser(store, f)

    p.startDoc()
    p.feed(string)
    f = p.endDoc()

    f = f.close()
    return f



def loadFactN3(formulaTMS, string, closureMode=""):
    if loadFactFormula.pClosureMode:
        closureMode += "p"
    store = formulaTMS.workingContext.store
    f = store.newFormula()
    f.setClosureMode(closureMode)    
    f = parseN3(store, f, string)
    formulaTMS.getThing(f).assume()
    formulaTMS.assumedStrings.append(formulaTMS.workingContext.newLiteral(string, dt=n3NS))
    return f    

loadFactFormula.pClosureMode = False



baseFactsURI = 'http://dig.csail.mit.edu/TAMI/2007/amord/base-assumptions.ttl'
baseRulesURI = 'http://dig.csail.mit.edu/TAMI/2007/amord/base-rules.air_2_0.ttl'

#baseFactsURI =
#baseRulesURI = 'data:text/rdf+n3;charset=utf-8,' # quite empty

store = llyn.RDFStore()

n3NS = store.newSymbol('http://www.w3.org/2000/10/swap/grammar/n3#n3')

def testPolicy(logURIs, policyURIs, logFormula=None, ruleFormula=None, filterProperties=['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with']):
    trace, result = runPolicy(logURIs, policyURIs, logFormula=logFormula, ruleFormula=ruleFormula, filterProperties=filterProperties)
    return trace.n3String()

def runPolicy(logURIs, policyURIs, logFormula=None, ruleFormula=None, filterProperties=['http://dig.csail.mit.edu/TAMI/2007/amord/air#compliant-with', 'http://dig.csail.mit.edu/TAMI/2007/amord/air#non-compliant-with']):
    global baseFactsURI, baseRulesURI
    if OFFLINE[0]:
        baseFactsURI = uripath.join(uripath.base(),
                                      baseFactsURI.replace('http://dig.csail.mit.edu/TAMI',
                                                           '../../..'))
        baseRulesURI = uripath.join(uripath.base(),
                                      baseRulesURI.replace('http://dig.csail.mit.edu/TAMI',
                                                           '../../..'))
        logURIs = map(lambda x: uripath.join(uripath.base(), x), logURIs)
        policyURIs = map(lambda x: uripath.join(uripath.base(), x), policyURIs)
    import time
    formulaTMS = setupTMS(store)
    workingContext = formulaTMS.workingContext

## We are done with cwm setup
    startTime = time.time()
    
    logFormulae = []
    if logFormula is not None:
        logFormulae.append(loadFactN3(formulaTMS, logFormula, ""))
    for logURI in logURIs:
        logFormulae.append(loadFactFormula(formulaTMS, logURI, "")) # should it be "p"?

    baseFactsFormula = loadFactFormula(formulaTMS, baseFactsURI)

    eventLoop = EventLoop()


    policyFormulae = []
    if ruleFormula is not None:
        policyFormulae.append(parseN3(store, store.newFormula(), ruleFormula))
    for policyURI in policyURIs:
        policyFormulae.append(store.load(policyURI))
    baseRulesFormula = store.load(baseRulesURI)

#    rdfsRulesFormula = store.load('http://python-dlp.googlecode.com/files/pD-rules.n3')
    
    rdf = workingContext.newSymbol('http://www.w3.org/1999/02/22-rdf-syntax-ns')
    owl = workingContext.newSymbol('http://www.w3.org/2002/07/owl')
    p = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/amord/air')
    u = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s0/university')
    s9 = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s9/run/s9-policy')
    s9Log = workingContext.newSymbol('http://dig.csail.mit.edu/TAMI/2007/s9/run/s9-log')


#    AIRFormula = store.load(p.uriref() + '.ttl')
#    formulaTMS.getThing(AIRFormula).assume()
        
#    formulaTMS.getTriple(p['data'], rdf['type'], owl['TransitiveProperty']).assume()

    compileStartTime = time.time()

    rdfsRules = [] #[Rule.compileCwmRule(eventLoop, formulaTMS, rdfsRulesFormula, x) for x in rdfsRulesFormula.statementsMatching(pred=store.implies)]



    allRules = []
    allGoalRules = []
#    # We need to 'flatten' the policy formulae before we can compile it.
    policyFormula = store.mergeFormulae(policyFormulae)
    for pf in [policyFormula] + [baseRulesFormula]:
#    for pf in policyFormulae + [baseRulesFormula]:
        if pf is baseRulesFormula: ## Realy bad hack!
            base=True
        else:
            base=False
        policies, rules, goal_rules, cwm_rules = Rule.compileFormula(eventLoop, formulaTMS, pf, base=base)
        formulaTMS.assumedPolicies.extend(policies)
        allRules += rules
        allRules += cwm_rules
        allGoalRules += goal_rules
    print 'rules = ', allRules
    print 'goal rules = ', goal_rules
    ruleAssumptions = []
    for rule in rdfsRules + allRules + allGoalRules:
        a  = formulaTMS.getThing(rule)
        ruleAssumptions.append(a)
        a.assume()

    eventStartTime = time.time()
    Formula._isReasoning = True
    FormulaTMS.tracking = False
    while eventLoop:
        eventLoop.next()
    Formula._isReasoning = False
    print workcount

# See how long it took (minus output)
    now = time.time()
    totalTime = now - startTime
    print 'time reasoning took=', totalTime
    print '  of which %s was after loading, and %s was actual reasoning' % (now-compileStartTime, now-eventStartTime)

#    rete.printRete()
    triples = list(reduce(lambda x, y: x + y, [workingContext.statementsMatching(pred=workingContext.newSymbol(property)) for property in filterProperties]))
    if triples:
        print 'I can prove the following compliance statements:'
    else:
        print 'There is nothing to prove'
        
    tmsNodes = [formulaTMS.getTriple(triple.subject(), triple.predicate(), triple.object(), None) for triple in triples]
    reasons, premises = supportTrace(tmsNodes)
    reasons, premises = removeFormulae(reasons, premises)
    strings = simpleTraceOutput(tmsNodes, reasons, premises)
    print '\n'.join(strings)
    f = rdfTraceOutput(store, tmsNodes, reasons, premises, Rule)
#    import diag
#    diag.chatty_flag = 1000
    return f, workingContext 


knownScenarios = {
    's0' : ( ['http://dig.csail.mit.edu/TAMI/2007/s0/log.n3'],
             ['http://dig.csail.mit.edu/TAMI/2007/s0/mit-policy.n3'] ),
    's0Local' : ( ['../../s0/log.n3'],
                  [  '../../s0/mit-policy.n3'] ),
    's9var2Local' : (['../../s9/variation2/log.n3'],
                     ['../../s9/variation2/policy.n3']),
    's9var1Local' : (['../../s9/variation1/log.n3'],
                     ['../../s9/variation1/policy1.n3', '../../s9/variation1/policy2.n3']),
#                     ['../../s9/variation1/policy.n3']),
#                     ['../../s9/variation1/demo-policy.n3']),
    'arl1Local' : (['../../../../2008/ARL/log.n3'],
                     ['../../../../2008/ARL/udhr-policy.n3']),    
     'arl2Local' : (['../../../../2008/ARL/log.n3'],
                     ['../../../../2008/ARL/unresol-policy.n3']),    
    's4' : (['http://dig.csail.mit.edu/TAMI/2006/s4/background.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/categories.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/data-schema.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-bru.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-crs.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/fbi-tsrs.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/purposes.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/s4.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/tsa-sfdb.ttl',
'http://dig.csail.mit.edu/TAMI/2006/s4/usms-win.ttl'
],
            ['http://dig.csail.mit.edu/TAMI/2006/s4/privacy-act.ttl']),
    'ucsd' : (['http://dig.csail.mit.edu/2008/Talks/0513-UCSD/simple/policy-explicit.n3'],
            ['http://dig.csail.mit.edu/2008/Talks/0513-UCSD/simple/data1.n3']),
    'arl1' : (['http://dig.csail.mit.edu/2008/ARL/log.n3'],
                     ['http://dig.csail.mit.edu/2008/ARL/udhr-policy.n3']), 
    'arl2' : (['http://dig.csail.mit.edu/2008/ARL/log.n3'],
             ['http://dig.csail.mit.edu/2008/ARL/unresol-policy.n3']),

}

def runScenario(s, others=[]):
    if s[-5:] == 'Local':
        OFFLINE[0] = True
    if s == 'test':
        rules = others[0:1]
        facts = others[1:]
    elif s not in knownScenarios:
        facts = ['http://dig.csail.mit.edu/TAMI/2007/%s/log.n3' % s]
        rules = ['http://dig.csail.mit.edu/TAMI/2007/%s/policy.n3' % s]
 #       raise ValueError("I don't know about scenario %s" % s)
    else:
        facts, rules = knownScenarios[s]
    return testPolicy(facts, rules)

def main():
    global MM
    from optparse import OptionParser
    usage = "usage: %prog [options] scenarioName"
    parser = OptionParser(usage)
    parser.add_option('--profile', dest="profile", action="store_true", default=False,
                      help="""Instead of displaying output, display profile information.
 This requires the hotshot module, and is a bit slow
""")
    parser.add_option('--psyco', '-j', dest='psyco', action="store_true", default=False,
                      help="""Try to use psyco to speed up the program.
Don't try to do this while profiling --- it won't work.
If you do not have psyco, it will throw an ImportError

There are no guarentees this will speed things up instead of
slowing them down. In fact, it seems to slow them down quite
a bit right now
""")
    parser.add_option('--full-unify', dest='fullUnify', action="store_true", default=False,
                      help="""Run full unification (as opposed to simple implication)
of goals. This may compute (correct) answers it would otherwise miss
It is much more likely to simply kill your performance at this time
""")
    parser.add_option('--lookup-ontologies', '-L', dest="lookupOntologies", action="store_true", default=False,
                      help="""Set the cwm closure flag of "p" on all facts loaded.
This will load the ontologies for all properties, until that
converges. This may compute (correct) answers it would otherwise miss
It is much more likely to simply kill your performance at this time.
It may even cause the computation to fail, if a URI 404's or is not RDF.
""")
    parser.add_option('--reasoner', '-r', dest="reasoner", action="store", default="rete",
                      help="""Which reasoner to chose. Current options are
'rete' and 'treat' (without the quotes). The default is 'rete',
which seems faster right now. 'treat' is likely more extensible
for the future, but may still be buggy.
""")

    (options, args) = parser.parse_args()
    if not args:
        args = ['s0']
    call = lambda : runScenario(args[0], args[1:])
    MM = eval(options.reasoner)
    if options.lookupOntologies:
        loadFactFormula.pClosureMode = True
    if options.fullUnify:
        rete.fullUnify = True
    if options.psyco:
        if options.profile:
            raise ValueError("I can't profile with psyco")
        import psyco
        psyco.log()
        psyco.full()
##        psyco.profile(0.05, memory=100)
##        psyco.profile(0.2)
    if options.profile:
        import sys
        stdout = sys.stdout
        import hotshot, hotshot.stats
        import tempfile
        fname = tempfile.mkstemp()[1]
        print fname
        sys.stdout = null = file('/dev/null', 'w')
        profiler = hotshot.Profile(fname)
        profiler.runcall(call)
        profiler.close()
        sys.stdout = stdout
        null.close()
        print 'done running. Ready to do stats'
        stats = hotshot.stats.load(fname)
        stats.strip_dirs()
        stats.sort_stats('cumulative', 'time', 'calls')
        stats.print_stats(60)
        stats.sort_stats('time', 'cumulative', 'calls')
        stats.print_stats(60)
    else:
        print call()

        


if __name__ == '__main__':
    main()

