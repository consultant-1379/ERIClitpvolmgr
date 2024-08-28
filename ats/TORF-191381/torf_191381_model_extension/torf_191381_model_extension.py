from litp.core.extension import ModelExtension
from litp.core.model_type import ItemType, Property


class Torf191381Extension(ModelExtension):
    def define_item_types(self):
        return [ItemType("torf_191381_disk",
                         extend_item="disk-base",
                         name=Property("any_string"),
                         size=Property("any_string"),
                         uuid=Property("any_string"),
                         bootable=Property("basic_boolean"))]
