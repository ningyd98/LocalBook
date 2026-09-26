//
//  AIPopoverController.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import AppKit
import SwiftUI

/// Controller for managing AI Popover
class AIPopoverController {
    private var popover: NSPopover?

    /// Show AI popover relative to a view
    func showPopover(
        relativeTo positioningView: NSView,
        highlightID: UUID,
        sourceID: UUID,
        context: String
    ) {
        // Close existing popover if any
        popover?.close()

        // Create new popover
        let newPopover = NSPopover()
        newPopover.contentSize = NSSize(width: 420, height: 560)
        newPopover.behavior = .transient
        newPopover.animates = true

        // Create SwiftUI view
        let contentView = AIPopoverView(
            highlightID: highlightID,
            sourceID: sourceID,
            context: context
        )

        // Wrap in hosting controller
        let hostingController = NSHostingController(rootView: contentView)
        newPopover.contentViewController = hostingController

        // Show popover
        newPopover.show(
            relativeTo: positioningView.bounds,
            of: positioningView,
            preferredEdge: .maxY
        )

        self.popover = newPopover
    }

    /// Close the popover
    func closePopover() {
        popover?.close()
        popover = nil
    }

    /// Check if popover is currently shown
    var isShown: Bool {
        return popover?.isShown ?? false
    }
}
