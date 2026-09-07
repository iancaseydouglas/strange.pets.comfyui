import { app } from "../../scripts/app.js";

const PREFIX = "input_image";

const IMAGE_LIMITS = {
    BFLFlux2Pro: 8,
    BFLFlux2Max: 8,
    BFLFlux2Flex: 8,
    BFLFlux2Klein9B: 4,
    BFLFlux2Klein4B: 4,
};

function chainCallback(object, property, callback) {
    if (object === undefined) {
        return;
    }
    if (property in object) {
        const original = object[property];
        object[property] = function () {
            const result = original.apply(this, arguments);
            callback.apply(this, arguments);
            return result;
        };
    } else {
        object[property] = callback;
    }
}

function slotName(index) {
    return index === 1 ? PREFIX : `${PREFIX}_${index}`;
}

function imageSlots(node) {
    return (node.inputs || []).filter(
        (input) => input.name === PREFIX || input.name.startsWith(`${PREFIX}_`)
    );
}

function syncImageInputs(node, max) {
    let slots = imageSlots(node);
    if (slots.length === 0) {
        return;
    }
    let changed = false;

    while (
        slots.length > 1 &&
        slots[slots.length - 1].link == null &&
        slots[slots.length - 2].link == null
    ) {
        node.removeInput(node.inputs.indexOf(slots[slots.length - 1]));
        slots = imageSlots(node);
        changed = true;
    }

    if (slots.length < max && slots[slots.length - 1].link != null) {
        const input = node.addInput(slotName(slots.length + 1), "IMAGE");
        input.optional = true;
        if (typeof LiteGraph !== "undefined" && LiteGraph.HollowCircle !== undefined) {
            input.shape = LiteGraph.HollowCircle;
        }
        changed = true;
    }

    if (!changed) {
        return;
    }

    const computed = node.computeSize();
    node.setSize([Math.max(node.size[0], computed[0]), Math.max(node.size[1], computed[1])]);
    node.setDirtyCanvas(true, true);
}

app.registerExtension({
    name: "bfl.flux2.dynamicImageInputs",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        const max = IMAGE_LIMITS[nodeData.name];
        if (!max) {
            return;
        }

        chainCallback(nodeType.prototype, "onNodeCreated", function () {
            const first = imageSlots(this)[0];
            if (first) {
                first.optional = true;
            }
            syncImageInputs(this, max);
        });

        chainCallback(nodeType.prototype, "onConfigure", function () {
            syncImageInputs(this, max);
        });

        chainCallback(nodeType.prototype, "onConnectionsChange", function (type) {
            if (type !== LiteGraph.INPUT || app.configuringGraph) {
                return;
            }
            syncImageInputs(this, max);
        });
    },
});
