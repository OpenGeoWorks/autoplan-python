from models.plan import PlanProps, PlanType


class LayoutPlan(PlanProps):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.type != PlanType.LAYOUT:
            raise ValueError("LayoutPlan must have type PlanType.LAYOUT")