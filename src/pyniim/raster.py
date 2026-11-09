from .models import ModelsManager


class NiimbotRaster:
    def __init__(self, model="M2_H"):
        self.model_obj = ModelsManager().get_model_by_identifier(model)
        self.model = self.model_obj.identifier
        self.job = None
