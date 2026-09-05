"""Signal library. Each signal maps a price panel to a target exposure panel
(values in [-1, 1], fraction of sleeve NAV per asset), using only information
available at time t (all operations are backward-looking / causal).
"""
