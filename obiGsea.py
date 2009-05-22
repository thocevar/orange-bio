import orange
import numpy
import stats
import random
import time
import math, os
from obiExpression import *
from obiGeneSets import *

"""
Gene set enrichment analysis.

Author: Marko Toplak
"""

def iset(data):
    """
    Is data orange.ExampleTable?
    """
    return isinstance(data, orange.ExampleTable)

def issequencens(x):
    "Is x a sequence and not string ? We say it is if it has a __getitem__ method and is not string."
    return hasattr(x, '__getitem__') and not isinstance(x, basestring)

def mean(l):
    return float(sum(l))/len(l)

def rankingFromOrangeMeas(meas):
    """
    Creates a function that sequentally ranks all attributes and returns
    results in a list. Ranking function is build out of 
    orange.MeasureAttribute.
    """
    return lambda d: [ meas(i,d) for i in range(len(d.domain.attributes)) ]

def orderedPointersCorr(lcor):
    """
    Return a list of integers: indexes in original
    lcor. Elements in the list are ordered by
    their lcor[i] value. Higher correlations first.
    """
    ordered = [ (i,a) for i,a in enumerate(lcor) ] #original pos + correlation
    ordered.sort(lambda x,y: cmp(y[1],x[1])) #sort by correlation, descending
    ordered = nth(ordered, 0) #contains positions in the original list
    return ordered

def enrichmentScoreRanked(subset, lcor, ordered, p=1.0, rev2=None):
    """
    Input data and subset. 
    
    subset: list of attribute indices of the input data belonging
        to the same set.
    lcor: correlations with class for each attribute in a list. 

    Returns enrichment score on given data.

    This implementation efficiently handles "sparse" genesets (that
    cover only a small subset of all genes in the dataset).
    """

    #print lcor

    subset = set(subset)

    if rev2 == None:
        def rev(l):
            return numpy.argsort(l)
        rev2 = rev(ordered)

    #add if gene is not in the subset
    notInA = -(1. / (len(lcor)-len(subset)))
    #base for addition if gene is in the subset
    cors = [ abs(lcor[i])**p for i in subset ]
    sumcors = sum(cors)

    #this should not happen
    if sumcors == 0.0:
        return (0.0, None)
    
    inAb = 1./sumcors

    ess = [0.0]
    
    map = {}
    for i in subset:
        orderedpos = rev2[i]
        map[orderedpos] = inAb*abs(lcor[i]**p)
        
    last = 0

    maxSum = minSum = csum = 0.0

    for a,b in sorted(map.items()):
        diff = a-last
        csum += notInA*diff
        last = a+1
        
        if csum < minSum:
            minSum = csum
        
        csum += b

        if csum > maxSum:
            maxSum = csum

    #finish it
    diff = (len(ordered))-last
    csum += notInA*diff

    if csum < minSum:
        minSum = csum

    #print "MY", (maxSum if abs(maxSum) > abs(minSum) else minSum)

    """
    #BY DEFINITION
    print "subset", subset

    for i in ordered:
        ess.append(ess[-1] + \
            (inAb*abs(lcor[i]**p) if i in subset else notInA)
        )
        if i in subset:
            print ess[-2], ess[-1]
            print i, (inAb*abs(lcor[i]**p))

    maxEs = max(ess)
    minEs = min(ess)
    
    print "REAL", (maxEs if abs(maxEs) > abs(minEs) else minEs, ess[1:])

    """
    return (maxSum if abs(maxSum) > abs(minSum) else minSum, [])

#from mOrngData
def shuffleAttribute(data, attribute, locations):
    """
    Destructive!
    """
    attribute = data.domain[attribute]
    l = [None]*len(data)
    for i in range(len(data)):
        l[locations[i]] = data[i][attribute]
    for i in range(len(data)):
        data[i][attribute] = l[i]

def shuffleClass(datai, rands=0):
    """
    Returns a dataset with values of class attribute randomly shuffled.
    If multiple dataset are on input shuffle them all with the same random seed.
    """
    def shuffleOne(data):
        rand = random.Random(rands)
        d2 = orange.ExampleTable(data.domain, data)
        locations = range(len(data))
        rand.shuffle(locations)
        shuffleAttribute(d2, d2.domain.classVar, locations)
        return d2

    if iset(datai):
        return shuffleOne(datai)
    else:
        return [ shuffleOne(data) for data in datai ]

def shuffleList(l, rand=random.Random(0)):
    """
    Returns a copy of a shuffled input list.
    """
    import copy
    l2 = copy.copy(l)
    rand.shuffle(l2)
    return l2

def shuffleAttributes(data, rand=random.Random(0)):
    """
    Returns a dataset with a new attribute order.
    """
    natts = shuffleList(list(data.domain.attributes), rand)
    dom2 = orange.Domain(natts, data.domain.classVar)
    d2 = orange.ExampleTable(dom2, data)
    return d2

def gseapval(es, esnull):
    """
    From article (PNAS):
    estimate nominal p-value for S from esnull by using the positive
    or negative portion of the distribution corresponding to the sign 
    of the observed ES(S).
    """
    
    try:
        if es < 0:
            return float(len([ a for a in esnull if a <= es ]))/ \
                len([ a for a in esnull if a < 0])    
        else: 
            return float(len([ a for a in esnull if a >= es ]))/ \
                len([ a for a in esnull if a >= 0])
    except:
        return 1.0


def enrichmentScore(data, subset, rankingf):
    """
    Returns enrichment score and running enrichment score.
    """
    lcor = rankingf(data)
    ordered = orderedPointersCorr(lcor)
    es,l = enrichmentScoreRanked(subset, lcor, ordered)
    return es,l

def gseaE(data, subsets, rankingf=None, \
        n=100, permutation="class", **kwargs):
    """
    Run GSEA algorithm on an example table.

    data: orange example table. 
    subsets: list of distinct subsets of data.
    rankingf: function that returns correlation to class of each 
        variable.
    n: number of random permutations to sample null distribution.
    permutation: "class" for permutating class, else permutate attribute 
        order.

    """

    if not rankingf:
        rankingf=rankingFromOrangeMeas(MA_signalToNoise())

    enrichmentScores = []
 
    lcor = rankingf(data)
    #print lcor

    ordered = orderedPointersCorr(lcor)

    def rev(l):
        return numpy.argsort(l)

    rev2 = rev(ordered)

    for subset in subsets:
        es = enrichmentScoreRanked(subset, lcor, ordered, rev2=rev2)[0]
        enrichmentScores.append(es)

    runOptCallbacks(kwargs)

    #print "PERMUTATION", permutation

    enrichmentNulls = [ [] for a in range(len(subsets)) ]

    for i in range(n):

        if permutation == "class":
            d2 = shuffleClass(data, 2000+i) #fixed permutation
            r2 = rankingf(d2)

        else:
            r2 = shuffleList(lcor, random.Random(2000+i))

        ordered2 = orderedPointersCorr(r2)
        rev22 = rev(ordered2)
        for si,subset in enumerate(subsets):
            esn = enrichmentScoreRanked(subset, r2, ordered2, rev2=rev22)[0]
            enrichmentNulls[si].append(esn)

        runOptCallbacks(kwargs)

    return gseaSignificance(enrichmentScores, enrichmentNulls)


def runOptCallbacks(rargs):
    if "callback" in rargs:
        try:
            [ a() for a in rargs["callback"] ]
        except:
            rargs["callback"]()
            

def gseaR(rankings, subsets, n=100, **kwargs):
    """
    """

    if "permutation" in kwargs:
        if kwargs["permutation"] == "class":
            raise Exception("Only gene permutation possible")

    enrichmentScores = []
 
    ordered = orderedPointersCorr(rankings)
    
    def rev(l):
        return numpy.argsort(l)

    rev2 = rev(ordered)

    for subset in subsets:

        es = enrichmentScoreRanked(subset, rankings, ordered, rev2=rev2)[0]
        enrichmentScores.append(es)
    
    runOptCallbacks(kwargs)

    enrichmentNulls = [ [] for a in range(len(subsets)) ]

    for i in range(n):
        
        r2 = shuffleList(rankings, random.Random(2000+i))
        ordered2 = orderedPointersCorr(r2)
        rev22 = rev(ordered2)

        for si,subset in enumerate(subsets):

            esn = enrichmentScoreRanked(subset, r2, ordered2, rev2=rev22)[0]
            enrichmentNulls[si].append(esn)

        runOptCallbacks(kwargs)

    return gseaSignificance(enrichmentScores, enrichmentNulls)


def gseaSignificance(enrichmentScores, enrichmentNulls):

    #print enrichmentScores

    import time

    tb1 = time.time()

    enrichmentPVals = []
    nEnrichmentScores = []
    nEnrichmentNulls = []

    for i in range(len(enrichmentScores)):
        es = enrichmentScores[i]
        enrNull = enrichmentNulls[i]
        #print es, enrNull

        enrichmentPVals.append(gseapval(es, enrNull))

        #normalize the ES(S,pi) and the observed ES(S), separetely rescaling
        #the positive and negative scores by divident by the mean of the 
        #ES(S,pi)

        #print es, enrNull

        def normalize(s):
            try:
                if s == 0:
                    return 0.0
                if s >= 0:
                    meanPos = mean([a for a in enrNull if a >= 0])
                    #print s, meanPos
                    return s/meanPos
                else:
                    meanNeg = mean([a for a in enrNull if a < 0])
                    #print s, meanNeg
                    return -s/meanNeg
            except:
                return 0.0 #return if according mean value is uncalculable


        nes = normalize(es)
        nEnrichmentScores.append(nes)
        
        nenrNull = [ normalize(s) for s in enrNull ]
        nEnrichmentNulls.append(nenrNull)
 

    #print "First part", time.time() - tb1

    #FDR computation
    #create a histogram of all NES(S,pi) over all S and pi
    vals = reduce(lambda x,y: x+y, nEnrichmentNulls, [])


    def shorten(l, p=10000):
        """
        Take each len(l)/p element, if len(l)/p >= 2.
        """
        e = len(l)/p
        if e <= 1:
            return l
        else:
            return [ l[i] for i in xrange(0, len(l), e) ]

    #vals = shorten(vals) -> this can speed up second part. is it relevant TODO?

    """
    Use this null distribution to compute an FDR q value, for a given NES(S) =
    NES* >= 0. The FDR is the ratio of the percantage of all (S,pi) with
    NES(S,pi) >= 0, whose NES(S,pi) >= NES*, divided by the percentage of
    observed S wih NES(S) >= 0, whose NES(S) >= NES*, and similarly if NES(S)
    = NES* <= 0.
    """

    nvals = numpy.array(sorted(vals))
    nnes = numpy.array(sorted(nEnrichmentScores))

    #print "LEN VALS", len(vals), len(nEnrichmentScores)

    fdrs = []

    import operator

    for i in range(len(enrichmentScores)):

        nes = nEnrichmentScores[i]

        """
        #Strighfoward but slow implementation follows in comments.
        #Useful as code description.
        
        if nes >= 0:
            op0 = operator.ge
            opn = operator.ge
        else:
            op0 = operator.lt
            opn = operator.le

        allPos = [a for a in vals if op0(a,0)]
        allHigherAndPos = [a for a in allPos if opn(a,nes) ]

        nesPos = [a for a in nEnrichmentScores if op0(a,0) ]
        nesHigherAndPos = [a for a in nesPos if opn(a,nes) ]

        top = len(allHigherAndPos)/float(len(allPos)) #p value
        down = len(nesHigherAndPos)/float(len(nesPos))
        
        l1 = [ len(allPos), len(allHigherAndPos), len(nesPos), len(nesHigherAndPos)]

        allPos = allHigherAndPos = nesPos =  nesHigherAndPos = 1

        """

        #this could be speed up twice with the same accuracy! 
        if nes >= 0:
            allPos = int(len(vals) - numpy.searchsorted(nvals, 0, side="left"))
            allHigherAndPos = int(len(vals) - numpy.searchsorted(nvals, nes, side="left"))
            nesPos = len(nnes) - int(numpy.searchsorted(nnes, 0, side="left"))
            nesHigherAndPos = len(nnes) - int(numpy.searchsorted(nnes, nes, side="left"))
        else:
            allPos = int(numpy.searchsorted(nvals, 0, side="left"))
            allHigherAndPos = int(numpy.searchsorted(nvals, nes, side="right"))
            nesPos = int(numpy.searchsorted(nnes, 0, side="left"))
            nesHigherAndPos = int(numpy.searchsorted(nnes, nes, side="right"))
           
        """
        #Comparing results
        l2 = [ allPos, allHigherAndPos, nesPos, nesHigherAndPos ]
        diffs = [ l1[i]-l2[i] for i in range(len(l1)) ]
        sumd = sum( [ abs(a) for a in diffs ] )
        if sumd > 0:
            print nes > 0
            print "orig", l1
            print "modi", l2
        """

        try:
            top = allHigherAndPos/float(allPos) #p value
            down = nesHigherAndPos/float(nesPos)

            fdrs.append(top/down)
        except:
            fdrs.append(1000000000.0)
    
    #print "Whole part", time.time() - tb1

    return zip(enrichmentScores, nEnrichmentScores, enrichmentPVals, fdrs)

import obiGene

def nth(l,n): return [ a[n] for a in l ]

def itOrFirst(data):
    """ Returns input if input is of type ExampleTable, else returns first
    element of the input list """
    if iset(data):
        return data
    else:
        return data[0]

def wrap_in_list(data):
    """ Wraps orange.ExampleTable in a list """
    if iset(data):
        return [ data ]
    else:
        return data

def takeClasses(datai, classValues=None):
    """
    Function joins class groups specified in an input pair
    classValues. Each element of the pair is a list of class
    values to be joined to first or second class. Group
    classes in two new class values. If classValues is not 
    specified, take only first two classes.

    Input data can be a single data set or a list of data sets
    with the same domain.

    Returns transformed data sets / data sets. 
    """

    cv = itOrFirst(datai).domain.classVar
    nclassvalues = None

    if cv and len(itOrFirst(datai)) > 1:
        oldcvals = [ a for a in cv.values ]
        
        if not classValues:
            classValues = [ oldcvals[0], oldcvals[1] ]

        toJoin = []

        for vals in classValues:
            if issequencens(vals):
                toJoin.append(list(vals))
            else:
                toJoin.append([vals])

        classValues = reduce(lambda x,y: x+y, toJoin)
        classValues = [ str(a) for a in classValues ] # ok class values

        #dictionary of old class -> new class
        mapval = {}
        nclassvalues = [] # need to preserver order

        for joinvals in toJoin:
            joinvalsn = "+".join([ str(val) for val in sorted(joinvals) ])
            nclassvalues.append(joinvalsn)

            for val in joinvals:
                mapval[str(val)] = joinvalsn

        #take only examples with classValues classes
        nclass = orange.EnumVariable(cv.name, values=nclassvalues)
        ndom = orange.Domain(itOrFirst(datai).domain.attributes, nclass)

        def removeAndTransformClasses(data):
            """
            Removes unnecessary class values and joines them according
            to function input.
            """
            examples = []
            for ex in data:
                if ex[cv] in classValues:
                    vals = [ ex[a] for a in data.domain.attributes ]
                    vals.append(mapval[str(ex[cv].value)])
                    examples.append(vals)

            return orange.ExampleTable(ndom, examples)

        if iset(datai):
            datai = removeAndTransformClasses(datai)
        else:
            datai = [ removeAndTransformClasses(data) for data in datai ]

    return datai

def removeBadAttributes(datai, atLeast=3):
    """
    Removes attributes which would obscure GSEA analysis.

    Attributes need to be continuous, they need to have
    at least one value. Remove other attributes.

    For the attribute to be valid, it needs to have at least
    [atLeast] values for every class value.

    Return transformed data set / data sets and ignored attributes.
    """

    def attrOk(a, data):
        """
        Attribute is ok if it is continouous and if containg
        at least atLest not unknown values.
        """

        a = data.domain.attributes.index(a)

        #can't
        if data.domain.attributes[a].varType != orange.VarTypes.Continuous:
            return False

        if len(data) == 1:

            vals = [ex[a].value for ex in data if not ex[a].isSpecial()]
            if len(vals) < 1:
                return False 
        
        if len(data) > 1 and data.domain.classVar and atLeast > 0:

            valc = [ [ex[a].value for ex in data \
                        if not ex[a].isSpecial() and ex[-1] == data.domain.classVar[i] \
                   ] for i in range(len(data.domain.classVar.values)) ]
            minl = min( [ len(a) for a in valc ])
            
            if minl < atLeast:
                #print "Less than atLeast"
                return False

        return True
    

    def notOkAttributes(data):
        ignored = []
        for a in data.domain.attributes:
            if not attrOk(a, data):
                #print "Removing", a
                ignored.append(a)
        return ignored
    
    ignored = []
    if iset(datai):
        ignored = set(notOkAttributes(datai))
    else:
        #ignore any attribute which is has less than atLeast values for each class
        #ignored = set(reduce(lambda x,y: x+y, [ notOkAttributes(data) for data in datai ]))

        #remove any attribute, which is ok in less than half of the dataset
        ignored = []
        for a in itOrFirst(datai).domain.attributes:
            attrOks = sum([ attrOk(a, data) for data in datai ])
            if attrOks < len(datai)/2:
                ignored.append(a)


    natts = [ a for a in itOrFirst(datai).domain.attributes if a not in ignored ]
    #print ignored, natts, set(ignored) & set(natts)

    ndom = orange.Domain(natts, itOrFirst(datai).domain.classVar)

    datao = None
    if iset(datai):
        datao = orange.ExampleTable(ndom, datai)
    else:
        datao = [ orange.ExampleTable(ndom, data) for data in datai ]

    return datao, ignored

def keepOnlyMeanAttrs(datai, atLeast=3, classValues=None):
    """
    Attributes need to be continuous, they need to have
    at least one value.

    In order of attribute to be valid, it needs to have at least
    [atLeast] values for every class value.

    Keep only specified classes - group them in two values.
    """    
    datai = takeClasses(datai, classValues=classValues)
    return removeBadAttributes(datai, atLeast=atLeast)

def data_single_meas_column(data):
    """ 
    Returns true if data seems to be in one column
    (float variables) only. This column should contain 
    the rankings
    """
    columns = [a for a in data.domain] +  [ data.domain.getmeta(a) for a in list(data.domain.getmetas()) ]
    floatvars = [ a for a in columns if a.varType == orange.VarTypes.Continuous ]
    if len(floatvars) == 1:
        return True
    else:
        return False

def transposeIfNeeded(data):
    """
    if we have log2ratio in a single value column, transpose the matrix
    i.e. we have a single column with a continous variable. first
    string variable then becomes the gene name
    """

    def transpose_data(data):
        columns = [a for a in data.domain] +  [ data.domain.getmeta(a) for a in list(data.domain.getmetas()) ]
        floatvars = [ a for a in columns if a.varType == orange.VarTypes.Continuous ]
        if len(floatvars) == 1:
            floatvar = floatvars[0]
            stringvar = [ a for a in columns if a.varType == 6 ][0]

            tup = [ (ex[stringvar].value, ex[floatvar].value) for ex in data ]
            newdom = orange.Domain([orange.FloatVariable(name=a[0]) for a in tup ], False)
            example = [ a[1] for a in tup ]
            ndata = orange.ExampleTable(newdom, [example])
            return ndata
        return data

    single = iset(data)

    transposed = [ transpose_data(d) for d in wrap_in_list(data) ]

    if single:
        return transposed[0]
    else:
        return transposed



class GSEA(object):

    def __init__(self, organism=None, matcher=None):
        self.genesets = {}
        self.organism = organism
        self.gsweights = {}
        self.namesToIndices = None

    def setData(self, data, classValues=None, atLeast=3, caseSensitive=False):
        """
        WARNING. DUE TO BAD DESIGN YOU MAY CALL THIS FUNCTION ONLY ONCE.
        """

        data = transposeIfNeeded(data)

        data, info = keepOnlyMeanAttrs(data, classValues=classValues, atLeast=atLeast)

        self.data = data
        attrnames = [ a.name for a in itOrFirst(self.data).domain.attributes ]
        self.gm = obiGene.matcher([obiGene.GMKEGG(self.organism, ignore_case=not caseSensitive)], 
            ignore_case=not caseSensitive, direct=True)
        self.gm.set_targets(attrnames)
 
    def addGeneset(self, genesetname, genes):
        """
        Add a single gene set. See addGenesets function.
        Solely for backwards compatibility.
        """
        self.addGenesets({ genesetname: genes })

    def addGenesets(self, gsdic):
        """
        Adds genesets from input dictionary. Also. performs gene matching. Adds
        to a self.genesets: key is genesetname, it's values are individual
        genes and match results.
        """
        for genesetname, genes in gsdic.iteritems():

            if genesetname in self.genesets:
                raise Exception("Geneset with the name " + \
                    + genesetname + " is already in genesets.")
            else:
                datamatch = filter(lambda x: x[1] != None, [ (gene, self.gm.umatch(gene)) for gene in genes])
                self.genesets[genesetname] = ( genes, datamatch )

    def selectGenesets(self, minSize=3, maxSize=1000, minPart=0.1):
        """ Returns a list of gene sets that have sizes in limits """

        def okSizes(orig, transl):
            """compares sizes of genesets to limitations"""
            if len(transl) >= minSize and len(transl) <= maxSize \
                and float(len(transl))/len(orig) >= minPart:
                return True
            return False

        return  dict( (a,(b,c)) for a,(b,c) in self.genesets.iteritems() if okSizes(b,c) )

    def genesIndices(self, genes):
        """
        Returns in attribute indices of given genes.
        Buffers locations dictionary.
        """
        if not self.namesToIndices:
            self.namesToIndices = dict( \
                (at.name, i) for i,at in enumerate(itOrFirst(self.data).domain.attributes))

        return [ self.namesToIndices[gname] for gname in genes ]

    def compute_gene_weights(self, gsweights, gsetsnum, nattributes):
        """
        Computes gene set weights for all specified weights.
        Expects gene sets in form { name: [ num_attributes ] }
        GSWeights are 
        """
        pass

    def to_gsetsnum(self, names):
        """
        Returns a dictionary of gene sets with given names in gsetnums format.
        """
        return dict( (name,self.genesIndices(nth(self.genesets[name][1],1))) for name in names)

    def compute(self, minSize=3, maxSize=1000, minPart=0.1, n=100, **kwargs):

        subsetsok = self.selectGenesets(minSize=minSize, maxSize=maxSize, minPart=minPart)

        geneweights = None

        gsetsnum = self.to_gsetsnum(subsetsok.keys())
        gsetsnumit = gsetsnum.items() #to fix order

        if len(gsetsnum) == 0:
            return {} # prevent pointless computation of attributee ranks

        if len(self.gsweights) > 0:
            #set geneset
            geneweights = [1]*len(data.domain.attributes)

        if len(itOrFirst(self.data)) > 1:
            gseal = gseaE(self.data, nth(gsetsnumit,1), n=n, geneweights=geneweights, **kwargs)
        else:
            rankings = [ self.data[0][at].native() for at in self.data.domain.attributes ]
            gseal = gseaR(rankings, nth(gsetsnumit,1), n=n, **kwargs)

        res = {}

        for name,gseale in zip(nth(gsetsnumit,0),gseal):
            rdict = {}
            rdict['es'] = gseale[0]
            rdict['nes'] = gseale[1]
            rdict['p'] = gseale[2]
            rdict['fdr'] = gseale[3]
            rdict['size'] = len(self.genesets[name][0])
            rdict['matched_size'] = len(self.genesets[name][1])
            rdict['genes'] = nth(self.genesets[name][1],1)
            res[name] = rdict
        

        return res

def runGSEA(data, organism, classValues=None, geneSets=None, n=100, permutation="class", minSize=3, maxSize=1000, minPart=0.1, atLeast=3, matcher=None, **kwargs):

    gso = GSEA(organism=organism, matcher=matcher)
    gso.setData(data, classValues=classValues, atLeast=atLeast)
    
    if geneSets == None:
        genesets = collections(default=True)

    for name,genes in geneSets.items():
        gso.addGeneset(name, genes)

    res1 = gso.compute(n=n, permutation=permutation, minSize=minSize, maxSize=maxSize, minPart=minPart, **kwargs)
    return res1

def etForAttribute(datal,a):
    """
    Builds an example table for a single attribute across multiple 
    example tables.
    """

    tables = len(datal)

    def getAttrVals(data, attr):
        dom2 = orange.Domain([data.domain[attr]], False)
        dataa = orange.ExampleTable(dom2, data)
        return [ a[0].native() for a in dataa ]

    domainl = []
    valuesl = []

    for id, data in enumerate(datal):
        v = getAttrVals(data,a)
        valuesl.append(v)
        domainl.append(orange.FloatVariable(name=("v"+str(id))))

    classvals = getAttrVals(data, datal[0].domain.classVar)
    valuesl += [ classvals ]

    dom = orange.Domain(domainl, datal[0].domain.classVar)
    examples = [ list(a) for a in zip(*valuesl) ]

    datat = orange.ExampleTable(dom, examples)

    return datat


def evaluateEtWith(fn, *args, **kwargs):
    """
    fn - evaluates example table given
    following arguments.
    """

    def newf(datal):
        res = []
        for a in datal[0].domain.attributes:
            et = etForAttribute(datal, a)
            res.append(fn(et, *args, **kwargs))
        return res

    return newf


def hierarchyOutput(results, limitGenes=50):
    """
    Transforms results for use by hierarchy output from GO.

    limitGenes - maximum number of genes on output.
    """
    trans = []
    
    print "OUTPUT"

    for name, res in results.items():
        try:
            second = name.split(' ')[2]
            name = second if second[:2] == 'GO' else name
        except:
            pass
        
        trans.append((name, abs(res["nes"]), res["matched_size"], res["size"], res["p"], min(res["fdr"], 1.0), res["genes"][:limitGenes]))

    return trans

if  __name__=="__main__":

    data = orange.ExampleTable("sterolTalkHepa.tab")
    gen1 = collections(['steroltalk.gmt', ':kegg:hsa'], default=False)

    import mMisc

    out = runGSEA(data, n=10, geneSets=gen1, permutation="gene", atLeast=3, organism="hsa")
    print "\n".join(map(str,sorted(out.items())))
    
