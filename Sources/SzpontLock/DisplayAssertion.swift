import Foundation
import IOKit.pwr_mgt

/// Keeps the display awake while the watchdog is armed or locked - the whole point
/// is that the screen stays on rather than blanking.
final class DisplayAssertion {
    private var assertionID: IOPMAssertionID = 0
    private var held = false

    func acquire(reason: String) {
        guard !held else { return }
        var id: IOPMAssertionID = 0
        let result = IOPMAssertionCreateWithName(
            kIOPMAssertionTypeNoDisplaySleep as CFString,
            IOPMAssertionLevel(kIOPMAssertionLevelOn),
            reason as CFString,
            &id
        )
        if result == kIOReturnSuccess {
            assertionID = id
            held = true
        }
    }

    func release() {
        guard held else { return }
        IOPMAssertionRelease(assertionID)
        assertionID = 0
        held = false
    }
}
