import unittest
import numpy as np
import pandas as pd
from quality_experiments import info_discreteness,allocation

class QualityTests(unittest.TestCase):
    def test_id(self):
        steady=pd.Series(np.arange(100.,301.))
        lumpy=pd.Series(np.r_[np.linspace(100,80,199),300.,300.])
        self.assertLess(info_discreteness(steady),info_discreteness(lumpy))
        self.assertIsNone(info_discreteness(steady.iloc[:50]))
    def test_risk_weights(self):
        rng=np.random.default_rng(42)
        r=pd.DataFrame(rng.normal(size=(126,10))*np.arange(1,11))
        iv,md,a=allocation(r)
        self.assertAlmostEqual(iv.sum(),1.)
        self.assertAlmostEqual(md.sum(),1.)
        self.assertGreater(iv[0],iv[-1])
        self.assertGreaterEqual(a["md_dr"]+1e-7,a["iv_dr"])
        self.assertTrue((md>=0).all())
