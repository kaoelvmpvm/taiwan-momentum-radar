import unittest
import numpy as np
from pit_persistence_experiments import gamma,persistent
class SourceRuleTests(unittest.TestCase):
    def test_curvature(self):
        t=np.linspace(0,1,240)
        a=100*(1+.5*t*t);b=100*(1+t-.5*t*t)
        self.assertGreater(gamma(a),0)
        self.assertLess(gamma(b),0)
        self.assertAlmostEqual(gamma(a),gamma(a*100))
    def test_persistent_only_previous_winner(self):
        old=[str(i) for i in range(10)]
        new=["4","2","1","0","3","5","6","7","8","9"]
        self.assertEqual(persistent(new,old),["2","1"])
