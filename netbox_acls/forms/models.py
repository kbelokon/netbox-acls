"""
Defines each django model's GUI form to add or edit objects for each django model.
"""

from dcim.models import Device, Interface, Region, Site, SiteGroup, VirtualChassis
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.utils.safestring import mark_safe
from ipam.models import Prefix
from netbox.forms import NetBoxModelForm
from utilities.forms import CommentField, DynamicModelChoiceField
from virtualization.models import (
    Cluster,
    ClusterGroup,
    ClusterType,
    VirtualMachine,
    VMInterface,
)

from ..choices import ACLTypeChoices
from .constants import (
    ERROR_MESSAGE_ACTION_REMARK_SOURCE_PREFIX_SET,
    ERROR_MESSAGE_NO_REMARK,
    ERROR_MESSAGE_REMARK_WITHOUT_ACTION_REMARK,
    HELP_TEXT_ACL_ACTION,
    HELP_TEXT_ACL_RULE_INDEX,
    HELP_TEXT_ACL_RULE_LOGIC,
)
from ..models import (
    AccessList,
    ACLExtendedRule,
    ACLInterfaceAssignment,
    ACLStandardRule,
)

__all__ = (
    "AccessListForm",
    "ACLInterfaceAssignmentForm",
    "ACLStandardRuleForm",
    "ACLExtendedRuleForm",
)


class AccessListForm(NetBoxModelForm):
    """
    GUI form to add or edit an AccessList.
    Requires a device, a name, a type, and a default_action.
    """

    # Device selector
    region = DynamicModelChoiceField(
        queryset=Region.objects.all(),
        required=False,
    )
    site_group = DynamicModelChoiceField(
        queryset=SiteGroup.objects.all(),
        required=False,
        label="Site Group",
    )
    site = DynamicModelChoiceField(
        queryset=Site.objects.all(),
        required=False,
        query_params={
            "region_id": "$region",
            "group_id": "$site_group",
        },
    )
    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        query_params={
            "region_id": "$region",
            "group_id": "$site_group",
            "site_id": "$site",
        },
    )

    # Virtual Chassis selector
    virtual_chassis = DynamicModelChoiceField(
        queryset=VirtualChassis.objects.all(),
        required=False,
        label="Virtual Chassis",
    )

    # Virtual Machine selector
    cluster_type = DynamicModelChoiceField(
        queryset=ClusterType.objects.all(),
        required=False,
    )

    cluster_group = DynamicModelChoiceField(
        queryset=ClusterGroup.objects.all(),
        required=False,
        query_params={
            "type_id": "$cluster_type",
        },
    )

    cluster = DynamicModelChoiceField(
        queryset=Cluster.objects.all(),
        required=False,
        query_params={
            "type_id": "$cluster_type",
            "group_id": "$cluster_group",
        },
    )

    virtual_machine = DynamicModelChoiceField(
        queryset=VirtualMachine.objects.all(),
        required=False,
        label="Virtual Machine",
        query_params={
            "cluster_type_id": "$cluster_type",
            "cluster_group_id": "$cluster_group",
            "cluster_id": "$cluster",
        },
    )

    comments = CommentField()

    class Meta:
        """
        Defines the Model and fields to be used by the form.
        """

        model = AccessList
        fields = (
            "region",
            "site_group",
            "site",
            "device",
            "virtual_machine",
            "virtual_chassis",
            "name",
            "type",
            "default_action",
            "comments",
            "tags",
        )
        help_texts = {
            "default_action": "The default behavior of the ACL.",
            "name": "The name uniqueness per device is case insensitive.",
            "type": mark_safe(
                "<b>*Note:</b> CANNOT be changed if ACL Rules are assoicated to this Access List.",
            ),
        }

    def __init__(self, *args, **kwargs):
        """
        Initializes the form
        """

        # Initialize helper selectors
        instance = kwargs.get("instance")
        initial = kwargs.get("initial", {}).copy()
        if instance:
            if isinstance(instance.assigned_object, Device):
                initial["device"] = instance.assigned_object
            elif isinstance(instance.assigned_object, VirtualChassis):
                initial["virtual_chassis"] = instance.assigned_object
            elif isinstance(instance.assigned_object, VirtualMachine):
                initial["virtual_machine"] = instance.assigned_object
        kwargs["initial"] = initial

        super().__init__(*args, **kwargs)

    def clean(self):
        """
        Validates form inputs before submitting:
          - Check if more than one host type selected.
          - Check if no hosts selected.
          - Check if duplicate entry. (Because of GFK.)
          - Check if Access List has no existing rules before change the Access List's type.
        """
        cleaned_data = super().clean()
        error_message = {}
        if self.errors.get("name"):
            return cleaned_data
        name = cleaned_data.get("name")
        acl_type = cleaned_data.get("type")
        device = cleaned_data.get("device")
        virtual_chassis = cleaned_data.get("virtual_chassis")
        virtual_machine = cleaned_data.get("virtual_machine")

        # Check if more than one host type selected.
        if (
            (device and virtual_chassis)
            or (device and virtual_machine)
            or (virtual_chassis and virtual_machine)
        ):
            raise forms.ValidationError(
                "Access Lists must be assigned to one host (either a device, virtual chassis or virtual machine) at a time.",
            )
        # Check if no hosts selected.
        if not device and not virtual_chassis and not virtual_machine:
            raise forms.ValidationError(
                "Access Lists must be assigned to a device, virtual chassis or virtual machine.",
            )

        if device:
            host_type = "device"
            existing_acls = AccessList.objects.filter(name=name, device=device).exists()
        elif virtual_machine:
            host_type = "virtual_machine"
            existing_acls = AccessList.objects.filter(
                name=name,
                virtual_machine=virtual_machine,
            ).exists()
        else:
            host_type = "virtual_chassis"
            existing_acls = AccessList.objects.filter(
                name=name,
                virtual_chassis=virtual_chassis,
            ).exists()

        # Check if duplicate entry.
        if (
            "name" in self.changed_data or host_type in self.changed_data
        ) and existing_acls:
            error_same_acl_name = (
                "An ACL with this name is already associated to this host."
            )
            error_message |= {
                host_type: [error_same_acl_name],
                "name": [error_same_acl_name],
            }
        # Check if Access List has no existing rules before change the Access List's type.
        if self.instance.pk and (
            (
                acl_type == ACLTypeChoices.TYPE_EXTENDED
                and self.instance.aclstandardrules.exists()
            )
            or (
                acl_type == ACLTypeChoices.TYPE_STANDARD
                and self.instance.aclextendedrules.exists()
            )
        ):
            error_message["type"] = [
                "This ACL has ACL rules associated, CANNOT change ACL type.",
            ]

        if error_message:
            raise forms.ValidationError(error_message)

        return cleaned_data

    def save(self, *args, **kwargs):
        """
        Set assigned object
        """
        self.instance.assigned_object = (
            self.cleaned_data.get("device")
            or self.cleaned_data.get("virtual_chassis")
            or self.cleaned_data.get("virtual_machine")
        )

        return super().save(*args, **kwargs)


class ACLInterfaceAssignmentForm(NetBoxModelForm):
    """
    GUI form to add or edit ACL Host Object assignments
    Requires an access_list, a name, a type, and a default_action.
    """

    device = DynamicModelChoiceField(
        queryset=Device.objects.all(),
        required=False,
        # query_params={
        # Need to pass ACL device to it
        # },
    )
    interface = DynamicModelChoiceField(
        queryset=Interface.objects.all(),
        required=False,
        query_params={
            "device_id": "$device",
        },
    )
    virtual_machine = DynamicModelChoiceField(
        queryset=VirtualMachine.objects.all(),
        required=False,
        # query_params={
        # Need to pass ACL device to it
        # },
        label="Virtual Machine",
    )
    vminterface = DynamicModelChoiceField(
        queryset=VMInterface.objects.all(),
        required=False,
        query_params={
            "virtual_machine_id": "$virtual_machine",
        },
        label="VM Interface",
    )
    # virtual_chassis = DynamicModelChoiceField(
    #    queryset=VirtualChassis.objects.all(),
    #    required=False,
    #    label='Virtual Chassis',
    # )
    access_list = DynamicModelChoiceField(
        queryset=AccessList.objects.all(),
        # query_params={
        #    'assigned_object': '$device',
        #    'assigned_object': '$virtual_machine',
        # },
        label="Access List",
        help_text=mark_safe(
            "<b>*Note:</b> Access List must be present on the device already.",
        ),
    )
    comments = CommentField()

    def __init__(self, *args, **kwargs):
        """
        Initialize helper selectors
        """

        instance = kwargs.get("instance")
        initial = kwargs.get("initial", {}).copy()
        if instance:
            if isinstance(instance.assigned_object, Interface):
                initial["interface"] = instance.assigned_object
                initial["device"] = "device"
            elif isinstance(instance.assigned_object, VMInterface):
                initial["vminterface"] = instance.assigned_object
                initial["virtual_machine"] = "virtual_machine"
        kwargs["initial"] = initial

        super().__init__(*args, **kwargs)

    class Meta:
        """
        Defines the Model and fields to be used by the form.
        """

        model = ACLInterfaceAssignment
        fields = (
            "access_list",
            "direction",
            "device",
            "interface",
            "virtual_machine",
            "vminterface",
            "comments",
            "tags",
        )
        help_texts = {
            "direction": mark_safe(
                "<b>*Note:</b> CANNOT assign 2 ACLs to the same interface & direction.",
            ),
        }

    def clean(self):
        """
        Validates form inputs before submitting:
          - Check if both interface and vminterface are set.
          - Check if neither interface nor vminterface are set.
          - Check that an interface's parent device/virtual_machine is assigned to the Access List.
          - Check that an interface's parent device/virtual_machine is assigned to the Access List.
          - Check for duplicate entry. (Because of GFK)
          - Check that the interface does not have an existing ACL applied in the direction already.
        """
        cleaned_data = super().clean()
        error_message = {}
        access_list = cleaned_data.get("access_list")
        direction = cleaned_data.get("direction")
        interface = cleaned_data.get("interface")
        vminterface = cleaned_data.get("vminterface")

        # Check if both interface and vminterface are set.
        if interface and vminterface:
            error_too_many_interfaces = "Access Lists must be assigned to one type of interface at a time (VM interface or physical interface)"
            error_message |= {
                "interface": [error_too_many_interfaces],
                "vminterface": [error_too_many_interfaces],
            }
        elif not (interface or vminterface):
            error_no_interface = (
                "An Access List assignment but specify an Interface or VM Interface."
            )
            error_message |= {
                "interface": [error_no_interface],
                "vminterface": [error_no_interface],
            }
        else:
            if interface:
                assigned_object = interface
                assigned_object_type = "interface"
                host_type = "device"
                host = Interface.objects.get(pk=assigned_object.pk).device
                assigned_object_id = Interface.objects.get(pk=assigned_object.pk).pk
            else:
                assigned_object = vminterface
                assigned_object_type = "vminterface"
                host_type = "virtual_machine"
                host = VMInterface.objects.get(pk=assigned_object.pk).virtual_machine
                assigned_object_id = VMInterface.objects.get(pk=assigned_object.pk).pk

            assigned_object_type_id = ContentType.objects.get_for_model(
                assigned_object,
            ).pk
            access_list_host = AccessList.objects.get(pk=access_list.pk).assigned_object

            # Check that an interface's parent device/virtual_machine is assigned to the Access List.
            if access_list_host != host:
                error_acl_not_assigned_to_host = (
                    "Access List not present on selected host."
                )
                error_message |= {
                    "access_list": [error_acl_not_assigned_to_host],
                    assigned_object_type: [error_acl_not_assigned_to_host],
                    host_type: [error_acl_not_assigned_to_host],
                }
            # Check for duplicate entry.
            if ACLInterfaceAssignment.objects.filter(
                access_list=access_list,
                assigned_object_id=assigned_object_id,
                assigned_object_type=assigned_object_type_id,
                direction=direction,
            ).exists():
                error_duplicate_entry = "An ACL with this name is already associated to this interface & direction."
                error_message |= {
                    "access_list": [error_duplicate_entry],
                    "direction": [error_duplicate_entry],
                    assigned_object_type: [error_duplicate_entry],
                }
            # Check that the interface does not have an existing ACL applied in the direction already.
            if ACLInterfaceAssignment.objects.filter(
                assigned_object_id=assigned_object_id,
                assigned_object_type=assigned_object_type_id,
                direction=direction,
            ).exists():
                error_interface_already_assigned = (
                    "Interfaces can only have 1 Access List assigned in each direction."
                )
                error_message |= {
                    "direction": [error_interface_already_assigned],
                    assigned_object_type: [error_interface_already_assigned],
                }

        if error_message:
            raise forms.ValidationError(error_message)
        return cleaned_data

    def save(self, *args, **kwargs):
        """
        Set assigned object
        """
        self.instance.assigned_object = self.cleaned_data.get(
            "interface",
        ) or self.cleaned_data.get("vminterface")
        return super().save(*args, **kwargs)


class ACLStandardRuleForm(NetBoxModelForm):
    """
    GUI form to add or edit Standard Access List.
    Requires an access_list, an index, and ACL rule type.
    See the clean function for logic on other field requirements.
    """

    access_list = DynamicModelChoiceField(
        queryset=AccessList.objects.all(),
        query_params={
            "type": ACLTypeChoices.TYPE_STANDARD,
        },
        help_text=mark_safe(
            "<b>*Note:</b> This field will only display Standard ACLs.",
        ),
        label="Access List",
    )
    source_prefix = DynamicModelChoiceField(
        queryset=Prefix.objects.all(),
        required=False,
        help_text=HELP_TEXT_ACL_RULE_LOGIC,
        label="Source Prefix",
    )

    fieldsets = (
        ("Access List Details", ("access_list", "description", "tags")),
        ("Rule Definition", ("index", "action", "remark", "source_prefix")),
    )

    class Meta:
        """
        Defines the Model and fields to be used by the form.
        """

        model = ACLStandardRule
        fields = (
            "access_list",
            "index",
            "action",
            "remark",
            "source_prefix",
            "tags",
            "description",
        )
        help_texts = {
            "index": HELP_TEXT_ACL_RULE_INDEX,
            "action": HELP_TEXT_ACL_ACTION,
            "remark": mark_safe(
                "<b>*Note:</b> CANNOT be set if source prefix OR action is set.",
            ),
        }

    def clean(self):
        """
        Validates form inputs before submitting:
          - Check if action set to remark, but no remark set.
          - Check if action set to remark, but source_prefix set.
          - Check remark set, but action not set to remark.
        """
        cleaned_data = super().clean()
        error_message = {}

        # No need to check for unique_together since there is no usage of GFK

        if cleaned_data.get("action") == "remark":
            # Check if action set to remark, but no remark set.
            if not cleaned_data.get("remark"):
                error_message["remark"] = [ERROR_MESSAGE_NO_REMARK]
            # Check if action set to remark, but source_prefix set.
            if cleaned_data.get("source_prefix"):
                error_message["source_prefix"] = [
                    ERROR_MESSAGE_ACTION_REMARK_SOURCE_PREFIX_SET,
                ]
        # Check remark set, but action not set to remark.
        elif cleaned_data.get("remark"):
            error_message["remark"] = [ERROR_MESSAGE_REMARK_WITHOUT_ACTION_REMARK]

        if error_message:
            raise forms.ValidationError(error_message)
        return cleaned_data


class ACLExtendedRuleForm(NetBoxModelForm):
    """
    GUI form to add or edit Extended Access List.
    Requires an access_list, an index, and ACL rule type.
    See the clean function for logic on other field requirements.
    """

    access_list = DynamicModelChoiceField(
        queryset=AccessList.objects.all(),
        query_params={
            "type": ACLTypeChoices.TYPE_EXTENDED,
        },
        help_text=mark_safe(
            "<b>*Note:</b> This field will only display Extended ACLs.",
        ),
        label="Access List",
    )

    source_prefix = DynamicModelChoiceField(
        queryset=Prefix.objects.all(),
        required=False,
        help_text=HELP_TEXT_ACL_RULE_LOGIC,
        label="Source Prefix",
    )
    destination_prefix = DynamicModelChoiceField(
        queryset=Prefix.objects.all(),
        required=False,
        help_text=HELP_TEXT_ACL_RULE_LOGIC,
        label="Destination Prefix",
    )
    fieldsets = (
        ("Access List Details", ("access_list", "description", "tags")),
        (
            "Rule Definition",
            (
                "index",
                "action",
                "remark",
                "source_prefix",
                "source_ports",
                "destination_prefix",
                "destination_ports",
                "protocol",
            ),
        ),
    )

    class Meta:
        """
        Defines the Model and fields to be used by the form.
        """

        model = ACLExtendedRule
        fields = (
            "access_list",
            "index",
            "action",
            "remark",
            "source_prefix",
            "source_ports",
            "destination_prefix",
            "destination_ports",
            "protocol",
            "tags",
            "description",
        )
        help_texts = {
            "action": HELP_TEXT_ACL_ACTION,
            "destination_ports": HELP_TEXT_ACL_RULE_LOGIC,
            "index": HELP_TEXT_ACL_RULE_INDEX,
            "protocol": HELP_TEXT_ACL_RULE_LOGIC,
            "remark": mark_safe(
                "<b>*Note:</b> CANNOT be set if action is not set to remark.",
            ),
            "source_ports": HELP_TEXT_ACL_RULE_LOGIC,
        }

    def clean(self):
        """
        Validates form inputs before submitting:
          - Check if action set to remark, but no remark set.
          - Check if action set to remark, but source_prefix set.
          - Check if action set to remark, but source_ports set.
          - Check if action set to remark, but destination_prefix set.
          - Check if action set to remark, but destination_ports set.
          - Check if action set to remark, but destination_ports set.
          - Check if action set to remark, but protocol set.
          - Check remark set, but action not set to remark.
        """
        cleaned_data = super().clean()
        error_message = {}

        # No need to check for unique_together since there is no usage of GFK

        if cleaned_data.get("action") == "remark":
            self._extracted_from_clean_20(cleaned_data, error_message)
        elif cleaned_data.get("remark"):
            error_message["remark"] = [ERROR_MESSAGE_REMARK_WITHOUT_ACTION_REMARK]

        if error_message:
            raise forms.ValidationError(error_message)
        return cleaned_data

    # TODO: Consolidate this function with the one in ACLStandardRuleForm
    def _extracted_from_clean_20(self, cleaned_data, error_message):
        # Check if action set to remark, but no remark set.
        if not cleaned_data.get("remark"):
            error_message["remark"] = [ERROR_MESSAGE_NO_REMARK]
        # Check if action set to remark, but source_prefix set.
        if cleaned_data.get("source_prefix"):
            error_message["source_prefix"] = [
                ERROR_MESSAGE_ACTION_REMARK_SOURCE_PREFIX_SET,
            ]
        # Check if action set to remark, but source_ports set.
        if cleaned_data.get("source_ports"):
            error_message["source_ports"] = [
                "Action is set to remark, Source Ports CANNOT be set.",
            ]
        # Check if action set to remark, but destination_prefix set.
        if cleaned_data.get("destination_prefix"):
            error_message["destination_prefix"] = [
                "Action is set to remark, Destination Prefix CANNOT be set.",
            ]
        # Check if action set to remark, but destination_ports set.
        if cleaned_data.get("destination_ports"):
            error_message["destination_ports"] = [
                "Action is set to remark, Destination Ports CANNOT be set.",
            ]
        # Check if action set to remark, but protocol set.
        if cleaned_data.get("protocol"):
            error_message["protocol"] = [
                "Action is set to remark, Protocol CANNOT be set.",
            ]
